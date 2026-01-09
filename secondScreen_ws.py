"""Second Screen - Phiên bản WebSocket (tối ưu cho USB).

So với WebRTC:
- Không có ICE/STUN/TURN overhead
- Không có jitter buffer delay
- Không encode lại qua VP8/VP9/H264 (dùng JPEG trực tiếp)
- Chất lượng hình ảnh cao hơn (JPEG 95%)
- Độ trễ thấp hơn đáng kể qua USB

Screen Capture Backends:
- D3D11 Desktop Duplication (dxcam): ~2-5ms/frame, 60+ FPS
- MSS (GDI): ~20-30ms/frame, ~25 FPS max

Cách dùng:
    python secondScreen_ws.py --usb --quality 95 --fps 60
"""

import argparse
import asyncio
import ctypes
import socket
import time
from typing import Dict, Tuple, Set, Optional
from concurrent.futures import ThreadPoolExecutor
import queue
import threading

import cv2
import mss
import numpy as np
from aiohttp import web, WSMsgType
import aiohttp

# Thử import dxcam cho D3D11 Desktop Duplication
try:
    import dxcam
    HAS_DXCAM = True
except ImportError:
    HAS_DXCAM = False
    print("⚠️  dxcam không được cài đặt. Sử dụng mss (chậm hơn).")
    print("   Để cài đặt: pip install dxcam")


# Cấu hình mặc định
CONFIG = {
    "fps": 60,
    "quality": 95,          # JPEG quality (1-100), cao hơn = đẹp hơn
    "usb_mode": False,
    "monitor_index": None,  # None = tự động chọn màn hình cuối
    "scale": 1.0,           # Scale factor (0.5 = 50% kích thước)
    "max_bandwidth_kbps": 500000,  # Bandwidth tối đa (KB/s) - 500MB/s for USB
    "adaptive": True,       # Tự động điều chỉnh quality theo bandwidth
    "use_dxcam": True,      # Sử dụng D3D11 Desktop Duplication (nếu có)
}


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_cursor_pos() -> Tuple[int, int]:
    """Lấy vị trí con trỏ chuột (Windows)."""
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def draw_cursor(frame: np.ndarray, cursor_x: int, cursor_y: int, region: Dict) -> np.ndarray:
    """Vẽ con trỏ chuột lên frame."""
    rel_x = cursor_x - region["left"]
    rel_y = cursor_y - region["top"]

    if 0 <= rel_x < region["width"] and 0 <= rel_y < region["height"]:
        size = 20
        pts = np.array([
            [rel_x, rel_y],
            [rel_x, rel_y + size],
            [rel_x + int(size * 0.6), rel_y + int(size * 0.75)],
        ], np.int32)
        cv2.fillPoly(frame, [pts], (255, 255, 255))
        cv2.polylines(frame, [pts], isClosed=True, color=(0, 0, 0), thickness=1)

    return frame


def build_monitor_region() -> Dict:
    """Chọn vùng màn hình cần capture."""
    with mss.mss() as sct:
        monitors = sct.monitors
        print("Danh sách màn hình mss.monitors:")
        for i, m in enumerate(monitors):
            print(f"  {i}: {m}")

        if CONFIG["monitor_index"] is not None:
            chosen_index = CONFIG["monitor_index"]
        else:
            chosen_index = len(monitors) - 1

        if chosen_index >= len(monitors) or chosen_index < 0:
            chosen_index = len(monitors) - 1

        mon = monitors[chosen_index]
        print(f"Đang dùng monitor index {chosen_index}: {mon}")
        return {
            "left": mon["left"],
            "top": mon["top"],
            "width": mon["width"],
            "height": mon["height"],
        }


class ScreenCapture:
    """Capture màn hình và encode thành JPEG - hỗ trợ D3D11 và MSS."""

    def __init__(self, region: Dict, fps: int, quality: int, scale: float = 1.0,
                 max_bandwidth_kbps: int = 3000, adaptive: bool = True,
                 use_dxcam: bool = True, monitor_index: int = 0):
        self.region = region
        self.fps = fps
        self.base_quality = quality
        self.quality = quality
        self.scale = scale
        self.max_bandwidth_kbps = max_bandwidth_kbps
        self.adaptive = adaptive
        self.frame_interval = 1.0 / fps
        self._frame_count = 0
        self._monitor_index = monitor_index
        
        # Lưu offset của monitor để tính cursor position
        self._monitor_left = region["left"]
        self._monitor_top = region["top"]
        
        # Thread pool cho capture (fallback cho mss)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="capture")
        self._local = threading.local()
        
        # Tracking cho adaptive quality
        self._bytes_sent = 0
        self._last_bandwidth_check = time.perf_counter()
        self._current_bandwidth_kbps = 0
        self._target_frame_size_kb = max_bandwidth_kbps / fps
        
        # Pre-calculate scaled dimensions
        self.scaled_width = int(region["width"] * scale)
        self.scaled_height = int(region["height"] * scale)
        
        # Pre-allocate encode params
        self._encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]
        
        # Khởi tạo dxcam nếu có
        self._dxcam_camera = None
        self._use_dxcam = False
        
        if use_dxcam and HAS_DXCAM:
            try:
                # dxcam.output_info() trả về string, không phải list
                # Cần parse hoặc probe để tìm devices/outputs có sẵn
                outputs_str = str(dxcam.output_info())
                print(f"dxcam outputs:\n{outputs_str}")
                
                # Probe để tìm tất cả devices và outputs có sẵn
                # Thử tạo camera với device_idx tăng dần cho đến khi lỗi
                output_mapping = []
                for dev_idx in range(10):  # Tối đa 10 devices
                    for out_idx in range(10):  # Tối đa 10 outputs per device
                        try:
                            # Thử tạo camera để kiểm tra device/output có tồn tại
                            test_cam = dxcam.create(
                                device_idx=dev_idx,
                                output_idx=out_idx,
                                output_color="BGR"
                            )
                            if test_cam is not None:
                                # Lấy thông tin về output này
                                output_mapping.append((dev_idx, out_idx))
                                # Đóng camera test
                                del test_cam
                                # dxcam có thể cache, cần reset
                                import gc
                                gc.collect()
                        except Exception:
                            # Không có output này, thử output tiếp theo
                            if out_idx == 0:
                                # Nếu output 0 không tồn tại, device này không có
                                break
                            continue
                    # Nếu device này không có output nào, dừng probe devices
                    if not any(d == dev_idx for d, o in output_mapping):
                        if dev_idx > 0:
                            break
                
                # Nếu probe không tìm được gì, thử mặc định device 0, output 0
                if not output_mapping:
                    output_mapping = [(0, 0)]
                
                print(f"dxcam: Found {len(output_mapping)} output(s):")
                for i, (dev, out) in enumerate(output_mapping):
                    print(f"  [{i}] Device[{dev}] Output[{out}]")
                
                # Chọn device và output dựa trên monitor_index
                # monitor_index từ mss: 0=all, 1=primary, 2=secondary, ...
                # Mapping: monitor_index N (với N >= 1) -> output N-1 trong danh sách
                if monitor_index is not None and monitor_index >= 1:
                    target_output_idx = monitor_index - 1  # mss index 1 -> output 0
                else:
                    target_output_idx = 0  # Mặc định output đầu tiên (primary)
                
                # Giới hạn trong phạm vi có sẵn
                if target_output_idx >= len(output_mapping):
                    target_output_idx = len(output_mapping) - 1
                    print(f"dxcam: Requested output not available, using output {target_output_idx}")
                
                dxcam_device, dxcam_output = output_mapping[target_output_idx]
                
                print(f"dxcam: Using Device[{dxcam_device}] Output[{dxcam_output}]")
                
                self._dxcam_camera = dxcam.create(
                    device_idx=dxcam_device,
                    output_idx=dxcam_output,
                    output_color="BGR"
                )
                
                # Bắt đầu capture với target FPS
                self._dxcam_camera.start(target_fps=fps, video_mode=True)
                self._use_dxcam = True
                print(f"✅ D3D11 Desktop Duplication (dxcam) initialized - Device {dxcam_device}, Output {dxcam_output}")
            except Exception as e:
                print(f"⚠️  dxcam initialization failed: {e}")
                import traceback
                traceback.print_exc()
                print("   Falling back to mss...")
                self._dxcam_camera = None
                self._use_dxcam = False
        
        if not self._use_dxcam:
            print("📺 Using mss (GDI) for screen capture")
        
        print(f"Target frame size: {self._target_frame_size_kb:.1f} KB @ {fps} FPS")
        print(f"Output resolution: {self.scaled_width}x{self.scaled_height}")

    def _get_sct(self) -> mss.mss:
        """Lấy mss instance cho thread hiện tại (thread-local)."""
        if not hasattr(self._local, 'sct'):
            self._local.sct = mss.mss()
        return self._local.sct

    def _adjust_quality(self, frame_size_kb: float):
        """Điều chỉnh quality dựa trên kích thước frame thực tế."""
        if not self.adaptive:
            return
        
        if frame_size_kb > self._target_frame_size_kb * 1.2:
            self.quality = max(20, self.quality - 5)
            self._encode_params[1] = self.quality
        elif frame_size_kb < self._target_frame_size_kb * 0.7:
            self.quality = min(self.base_quality, self.quality + 2)
            self._encode_params[1] = self.quality

    def _capture_dxcam(self) -> Optional[np.ndarray]:
        """Capture frame bằng dxcam (D3D11 Desktop Duplication)."""
        if self._dxcam_camera is None:
            return None
        
        # dxcam.get_latest_frame() trả về frame mới nhất
        frame = self._dxcam_camera.get_latest_frame()
        return frame

    def _capture_mss(self) -> np.ndarray:
        """Capture frame bằng mss (GDI)."""
        sct = self._get_sct()
        img = sct.grab(self.region)
        
        # Convert to numpy
        frame = np.frombuffer(img.bgra, dtype=np.uint8).reshape(
            img.height, img.width, 4
        )
        # BGRA -> BGR
        frame = np.ascontiguousarray(frame[:, :, :3])
        return frame

    def _draw_cursor(self, frame: np.ndarray) -> np.ndarray:
        """Vẽ con trỏ chuột lên frame."""
        cursor_x, cursor_y = get_cursor_pos()
        
        # Tính vị trí tương đối của cursor trên monitor đang capture
        # Windows cursor position là tọa độ tuyệt đối trên desktop ảo
        # Cần trừ đi offset của monitor để được vị trí relative
        rel_x = int((cursor_x - self._monitor_left) * self.scale)
        rel_y = int((cursor_y - self._monitor_top) * self.scale)
        
        if 0 <= rel_x < self.scaled_width and 0 <= rel_y < self.scaled_height:
            size = int(16 * self.scale)
            if size >= 4:
                pts = np.array([
                    [rel_x, rel_y],
                    [rel_x, rel_y + size],
                    [rel_x + int(size * 0.6), rel_y + int(size * 0.75)],
                ], np.int32)
                cv2.fillPoly(frame, [pts], (255, 255, 255))
                cv2.polylines(frame, [pts], isClosed=True, color=(0, 0, 0), thickness=1)
        
        return frame

    def _capture_and_encode(self) -> bytes:
        """Capture và encode frame (chạy trong thread pool)."""
        # Capture frame
        if self._use_dxcam:
            frame = self._capture_dxcam()
            if frame is None:
                # Nếu dxcam trả về None, thử lại
                return b""
        else:
            frame = self._capture_mss()
        
        # Scale nếu cần
        if self.scale < 1.0:
            frame = cv2.resize(frame, (self.scaled_width, self.scaled_height),
                               interpolation=cv2.INTER_NEAREST)

        # Vẽ con trỏ chuột
        frame = self._draw_cursor(frame)

        # Encode JPEG
        ok, buffer = cv2.imencode(".jpg", frame, self._encode_params)
        
        if ok:
            return buffer.tobytes()
        return b""

    async def capture_frame_async(self) -> bytes:
        """Capture frame async (không block event loop)."""
        loop = asyncio.get_event_loop()
        frame_bytes = await loop.run_in_executor(self._executor, self._capture_and_encode)
        
        if frame_bytes:
            frame_size_kb = len(frame_bytes) / 1024
            
            # Adaptive quality adjustment
            self._adjust_quality(frame_size_kb)
            
            # Tracking bandwidth
            self._bytes_sent += len(frame_bytes)
            self._frame_count += 1
            
            now = time.perf_counter()
            elapsed = now - self._last_bandwidth_check
            if elapsed >= 1.0:
                self._current_bandwidth_kbps = self._bytes_sent / 1024 / elapsed
                actual_fps = self._frame_count / elapsed
                backend = "D3D11" if self._use_dxcam else "MSS"
                print(f"FPS: {actual_fps:.1f} | {self.scaled_width}x{self.scaled_height} | Q:{self.quality} | "
                      f"{frame_size_kb:.0f}KB/f | BW:{self._current_bandwidth_kbps:.0f}KB/s | {backend}")
                self._bytes_sent = 0
                self._frame_count = 0
                self._last_bandwidth_check = now
        
        return frame_bytes

    def shutdown(self):
        """Cleanup resources."""
        self._executor.shutdown(wait=False)
        if self._dxcam_camera is not None:
            try:
                self._dxcam_camera.stop()
            except Exception:
                pass
            self._dxcam_camera = None


# Global state
region: Dict = {}
active_websockets: Set[web.WebSocketResponse] = set()

# Shared ScreenCapture instance
_shared_capture: Optional[ScreenCapture] = None
_capture_lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None
_capture_ref_count = 0


async def get_shared_capture() -> ScreenCapture:
    """Lấy shared ScreenCapture instance, tạo mới nếu chưa có."""
    global _shared_capture, _capture_ref_count
    
    if _shared_capture is None:
        _shared_capture = ScreenCapture(
            region=region,
            fps=CONFIG["fps"],
            quality=CONFIG["quality"],
            scale=CONFIG["scale"],
            max_bandwidth_kbps=CONFIG["max_bandwidth_kbps"],
            adaptive=CONFIG["adaptive"],
            use_dxcam=CONFIG["use_dxcam"],
            monitor_index=CONFIG["monitor_index"] or 0
        )
    
    _capture_ref_count += 1
    print(f"Capture ref count: {_capture_ref_count}")
    return _shared_capture


async def release_shared_capture():
    """Giảm ref count, shutdown nếu không còn client nào."""
    global _shared_capture, _capture_ref_count
    
    _capture_ref_count -= 1
    print(f"Capture ref count: {_capture_ref_count}")
    
    if _capture_ref_count <= 0:
        if _shared_capture is not None:
            _shared_capture.shutdown()
            _shared_capture = None
        _capture_ref_count = 0


# Raw socket server cho Android app
async def handle_raw_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Xử lý kết nối raw socket từ Android app."""
    addr = writer.get_extra_info('peername')
    print(f"📱 Raw socket connected: {addr}")
    
    capture = await get_shared_capture()
    
    try:
        while True:
            start_time = time.perf_counter()
            
            # Capture frame
            frame_data = await capture.capture_frame_async()
            
            if frame_data:
                # Gửi: 4 bytes kích thước (big-endian) + dữ liệu JPEG
                size_bytes = len(frame_data).to_bytes(4, byteorder='big')
                writer.write(size_bytes + frame_data)
                await writer.drain()
            
            # FPS control
            elapsed = time.perf_counter() - start_time
            sleep_time = capture.frame_interval - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
                
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    except Exception as e:
        print(f"Raw socket error: {e}")
    finally:
        await release_shared_capture()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        print(f"📱 Raw socket disconnected: {addr}")


async def start_raw_server(port: int):
    """Khởi động raw socket server."""
    server = await asyncio.start_server(handle_raw_client, '0.0.0.0', port)
    print(f"📱 Raw socket server listening on port {port}")
    async with server:
        await server.serve_forever()


async def index(request: web.Request) -> web.Response:
    """Trang HTML với WebSocket client."""
    html = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <title>Second Screen (WebSocket)</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no" />
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #000;
      display: flex;
      justify-content: center;
      align-items: center;
      height: 100vh;
      overflow: hidden;
      touch-action: none;
    }
    #screen {
      max-width: 100vw;
      max-height: 100vh;
      object-fit: contain;
    }
    #stats {
      position: fixed;
      top: 10px;
      left: 10px;
      color: #0f0;
      font-family: monospace;
      font-size: 14px;
      background: rgba(0,0,0,0.7);
      padding: 8px 12px;
      border-radius: 4px;
      z-index: 1000;
    }
    #fullscreenBtn {
      position: fixed;
      bottom: 20px;
      right: 20px;
      padding: 12px 20px;
      font-size: 16px;
      background: rgba(255,255,255,0.9);
      border: none;
      border-radius: 8px;
      cursor: pointer;
      z-index: 1000;
    }
    :fullscreen #fullscreenBtn,
    :-webkit-full-screen #fullscreenBtn { display: none; }
    :fullscreen #stats,
    :-webkit-full-screen #stats { display: none; }
  </style>
</head>
<body>
  <img id="screen" alt="Screen" />
  <div id="stats">Đang kết nối...</div>
  <button id="fullscreenBtn">⛶ Toàn màn hình</button>

  <script>
    const img = document.getElementById('screen');
    const stats = document.getElementById('stats');
    const fullscreenBtn = document.getElementById('fullscreenBtn');

    let frameCount = 0;
    let lastTime = performance.now();
    let fps = 0;
    let latency = 0;
    let wakeLock = null;

    // Wake Lock - Giữ màn hình luôn sáng
    async function requestWakeLock() {
      try {
        if ('wakeLock' in navigator) {
          wakeLock = await navigator.wakeLock.request('screen');
          console.log('Wake Lock activated - màn hình sẽ không tắt');
          wakeLock.addEventListener('release', () => {
            console.log('Wake Lock released');
          });
        }
      } catch (err) {
        console.log('Wake Lock error:', err.message);
      }
    }

    // Tự động request lại Wake Lock khi tab được focus
    document.addEventListener('visibilitychange', async () => {
      if (document.visibilityState === 'visible' && wakeLock === null) {
        await requestWakeLock();
      }
    });

    // Request Wake Lock ngay khi load
    requestWakeLock();

    // Fullscreen
    function toggleFullscreen() {
      const elem = document.documentElement;
      if (!document.fullscreenElement && !document.webkitFullscreenElement) {
        (elem.requestFullscreen || elem.webkitRequestFullscreen).call(elem);
      } else {
        (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      }
    }
    fullscreenBtn.addEventListener('click', toggleFullscreen);
    img.addEventListener('click', toggleFullscreen);

    // WebSocket connection
    const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${wsProtocol}//${location.host}/ws`);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => {
      stats.textContent = 'Đã kết nối!';
      console.log('WebSocket connected');
    };

    ws.onmessage = (event) => {
      const receiveTime = performance.now();
      
      // Tạo blob URL từ dữ liệu JPEG
      const blob = new Blob([event.data], { type: 'image/jpeg' });
      const url = URL.createObjectURL(blob);
      
      // Revoke URL cũ để tránh memory leak
      if (img.src.startsWith('blob:')) {
        URL.revokeObjectURL(img.src);
      }
      img.src = url;

      // Tính FPS
      frameCount++;
      const now = performance.now();
      if (now - lastTime >= 1000) {
        fps = frameCount;
        frameCount = 0;
        lastTime = now;
      }

      // Hiển thị stats
      const sizeKB = (event.data.byteLength / 1024).toFixed(1);
      stats.textContent = `FPS: ${fps} | Size: ${sizeKB} KB`;
    };

    ws.onerror = (err) => {
      stats.textContent = 'Lỗi kết nối!';
      console.error('WebSocket error:', err);
    };

    ws.onclose = () => {
      stats.textContent = 'Mất kết nối!';
      console.log('WebSocket closed');
      // Thử kết nối lại sau 2 giây
      setTimeout(() => location.reload(), 2000);
    };
  </script>
</body>
</html>
"""
    return web.Response(content_type="text/html", text=html)


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    """Xử lý kết nối WebSocket và stream frames với pipelining."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    active_websockets.add(ws)
    print(f"WebSocket connected. Active: {len(active_websockets)}")

    # Dùng shared capture instance
    capture = await get_shared_capture()

    try:
        # Pipelining: bắt đầu capture frame đầu tiên
        next_frame_task = asyncio.create_task(capture.capture_frame_async())
        
        while not ws.closed:
            start_time = time.perf_counter()
            
            # Lấy frame đã capture
            frame_data = await next_frame_task
            
            # Bắt đầu capture frame tiếp theo ngay lập tức (pipeline)
            next_frame_task = asyncio.create_task(capture.capture_frame_async())
            
            # Gửi frame hiện tại
            if frame_data:
                try:
                    await ws.send_bytes(frame_data)
                except ConnectionResetError:
                    break

            # Tính thời gian chờ
            elapsed = time.perf_counter() - start_time
            sleep_time = capture.frame_interval - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        # Cancel pending task
        if not next_frame_task.done():
            next_frame_task.cancel()
            try:
                await next_frame_task
            except asyncio.CancelledError:
                pass
        
        # Giảm ref count, chỉ shutdown khi không còn client nào
        await release_shared_capture()
        active_websockets.discard(ws)
        print(f"WebSocket disconnected. Active: {len(active_websockets)}")

    return ws


def main() -> None:
    global region

    parser = argparse.ArgumentParser(
        description="Second Screen (WebSocket) - Tối ưu cho USB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
  python secondScreen_ws.py --usb                         # Chế độ USB mặc định
  python secondScreen_ws.py --usb --fps 60 --scale 0.75   # 60 FPS, scale 75%
  python secondScreen_ws.py --usb --bandwidth 3000        # Giới hạn 3MB/s
  python secondScreen_ws.py --usb --quality 70 --fps 60   # Quality 70%, 60 FPS
  python secondScreen_ws.py --no-adaptive --quality 50    # Tắt adaptive, cố định quality
  python secondScreen_ws.py --no-dxcam                    # Dùng mss thay vì D3D11
        """
    )
    parser.add_argument("--usb", action="store_true", help="Chế độ USB (tối ưu latency)")
    parser.add_argument("--fps", type=int, default=60, help="FPS (mặc định: 60)")
    parser.add_argument("--quality", type=int, default=80, help="JPEG quality 1-100 (mặc định: 80)")
    parser.add_argument("--scale", type=float, default=1.0, help="Scale factor 0.1-1.0 (mặc định: 1.0)")
    parser.add_argument("--bandwidth", type=int, default=500000, help="Max bandwidth KB/s (mặc định: 500000)")
    parser.add_argument("--no-adaptive", action="store_true", help="Tắt adaptive quality")
    parser.add_argument("--no-dxcam", action="store_true", help="Không dùng D3D11 (dùng mss)")
    parser.add_argument("--port", type=int, default=8080, help="Port HTTP/WebSocket (mặc định: 8080)")
    parser.add_argument("--raw-port", type=int, default=5001, help="Port raw socket cho app Android (mặc định: 5001)")
    parser.add_argument("--monitor", type=int, default=None, help="Monitor index (mặc định: tự động)")
    args = parser.parse_args()

    # Cập nhật config
    CONFIG["fps"] = args.fps
    CONFIG["quality"] = max(1, min(100, args.quality))
    CONFIG["scale"] = max(0.1, min(1.0, args.scale))
    CONFIG["max_bandwidth_kbps"] = args.bandwidth
    CONFIG["adaptive"] = not args.no_adaptive
    CONFIG["usb_mode"] = args.usb
    CONFIG["monitor_index"] = args.monitor
    CONFIG["use_dxcam"] = not args.no_dxcam

    # Build region
    region = build_monitor_region()

    # Tạo app
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket_handler)

    ip = get_local_ip()
    port = args.port

    print()
    print("=" * 60)
    print("  SECOND SCREEN (WebSocket) - Tối ưu cho USB")
    print("=" * 60)
    print()
    print("⚡ ƯU ĐIỂM SO VỚI WebRTC:")
    print("   • Không có ICE/STUN overhead")
    print("   • Không có jitter buffer delay")
    print("   • JPEG trực tiếp (không encode VP8/H264)")
    print("   • Adaptive quality theo bandwidth")
    print()
    
    # Backend info
    backend = "D3D11 Desktop Duplication (dxcam)" if (HAS_DXCAM and CONFIG["use_dxcam"]) else "MSS (GDI)"
    print(f"🖥️  CAPTURE BACKEND: {backend}")
    if HAS_DXCAM and CONFIG["use_dxcam"]:
        print("   • ~2-5ms per frame (60+ FPS capable)")
    else:
        print("   • ~20-30ms per frame (~25 FPS max)")
        if not HAS_DXCAM:
            print("   • Cài dxcam để tăng tốc: pip install dxcam")
    print()
    
    print(f"⚙️  CẤU HÌNH:")
    print(f"   FPS: {CONFIG['fps']}")
    print(f"   JPEG Quality: {CONFIG['quality']}% (base)")
    print(f"   Scale: {CONFIG['scale']} ({int(CONFIG['scale']*100)}%)")
    print(f"   Max Bandwidth: {CONFIG['max_bandwidth_kbps']} KB/s")
    print(f"   Adaptive: {CONFIG['adaptive']}")
    print()
    print("📶 KẾT NỐI QUA WI-FI:")
    print(f"   http://{ip}:{port}")
    print()
    print("🔌 KẾT NỐI QUA USB (Android):")
    print("   1. Bật USB Debugging trên điện thoại")
    print("   2. Kết nối USB và chạy:")
    print(f"      adb reverse tcp:{port} tcp:{port}")
    print(f"      adb reverse tcp:{args.raw_port} tcp:{args.raw_port}")
    print("   3. Mở trình duyệt:")
    print(f"      http://localhost:{port}")
    print()
    print("📱 KẾT NỐI VỚI APP ANDROID:")
    print(f"   Raw socket port: {args.raw_port}")
    print("   App sẽ tự động kết nối đến localhost:{args.raw_port}")
    print()
    print("=" * 60)

    # Chạy cả web server và raw socket server
    async def run_servers():
        # Khởi động raw socket server trong background
        raw_server_task = asyncio.create_task(start_raw_server(args.raw_port))
        
        # Chạy web server (blocking)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        
        print(f"Servers running. Press Ctrl+C to stop.")
        
        try:
            await asyncio.Future()  # Run forever
        except asyncio.CancelledError:
            pass
        finally:
            raw_server_task.cancel()
            await runner.cleanup()
    
    try:
        asyncio.run(run_servers())
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == "__main__":
    main()
