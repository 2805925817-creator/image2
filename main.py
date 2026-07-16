import webview
from webview import FileDialog
import json
import os
import sys
import time
import base64
import io
import threading
import uuid
from openai import OpenAI
from PIL import Image

# 固定的中转站地址，不允许用户修改
BASE_URL = "https://tdyun.ai/v1"
# 历史记录最多保留的条数，超出后自动清理最旧的记录及其图片文件
MAX_HISTORY_ENTRIES = 300
# 侧边栏缩略图长边上限（像素），缩略图仅用于列表展示，无需原图分辨率
THUMB_MAX_SIZE = 240
# 窗口默认尺寸（首次启动或读取失败时使用），需与 create_window 的 min_size 保持合理关系
DEFAULT_WIN_WIDTH = 1040
DEFAULT_WIN_HEIGHT = 920


class Api:
    def __init__(self):
        self.config_path = self._get_config_path()
        self.history_dir = os.path.join(os.path.dirname(self.config_path), 'history_images')
        self.history_path = os.path.join(os.path.dirname(self.config_path), 'history.json')
        os.makedirs(self.history_dir, exist_ok=True)
        self.key = ""
        self.base_url = BASE_URL
        self.model = "gpt-image-2"
        self.theme = "dark"  # 界面主题：dark / light
        # 窗口尺寸/位置：记住用户上次手动调整的大小，下次启动时还原
        self.win_width = DEFAULT_WIN_WIDTH
        self.win_height = DEFAULT_WIN_HEIGHT
        self.win_x = None
        self.win_y = None
        self._load_config()

    def _get_config_path(self):
        if sys.platform == 'darwin' or sys.platform == 'win32':
            # macOS/Windows: 统一存到用户目录下的 ~/.tdyun（Windows 即 C:\Users\<用户名>\.tdyun）
            # 避免写入 .app 包内部或程序安装目录（权限/签名问题，且卸载重装时配置和历史图片不会丢失）
            data_dir = os.path.join(os.path.expanduser('~'), '.tdyun')
            os.makedirs(data_dir, exist_ok=True)
            return os.path.join(data_dir, 'config.json')
        elif getattr(sys, 'frozen', False):
            return os.path.join(os.path.dirname(sys.executable), 'config.json')
        else:
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')

    def _get_html_path(self):
        if getattr(sys, 'frozen', False):
            return os.path.join(sys._MEIPASS, 'index.html')
        else:
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')

    def _load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.key = config.get('api_key', '')
                    self.model = config.get('model', self.model)
                    self.theme = config.get('theme', self.theme)
                    win = config.get('window') or {}
                    self.win_width = self._sanitize_win_size(win.get('width'), DEFAULT_WIN_WIDTH)
                    self.win_height = self._sanitize_win_size(win.get('height'), DEFAULT_WIN_HEIGHT)
                    self.win_x = self._sanitize_win_pos(win.get('x'))
                    self.win_y = self._sanitize_win_pos(win.get('y'))
            except Exception:
                pass

    def _sanitize_win_size(self, value, default):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    def _sanitize_win_pos(self, value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def get_config(self):
        masked_key = ""
        if self.key:
            if len(self.key) > 8:
                masked_key = self.key[:4] + "****" + self.key[-4:]
            else:
                masked_key = "****"
        return {
            'base_url': self.base_url,
            'api_key_masked': masked_key,
            'has_config': bool(self.key),
            'model': self.model,
            'theme': self.theme
        }

    def _write_config(self):
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump({
                'api_key': self.key,
                'model': self.model,
                'theme': self.theme,
                'window': {
                    'width': self.win_width,
                    'height': self.win_height,
                    'x': self.win_x,
                    'y': self.win_y
                }
            }, f, ensure_ascii=False, indent=2)

    def save_config(self, api_key, model):
        self.key = api_key
        self.model = model
        try:
            self._write_config()
        except Exception as e:
            return {'success': False, 'error': str(e)}
        return {'success': True}

    def _save_window_geometry(self):
        """记录窗口关闭前的最终尺寸/位置，供下次启动还原；异常（如磁盘写入失败）静默忽略，不阻塞退出"""
        try:
            window = webview.windows[0]
            self.win_width = window.width
            self.win_height = window.height
            self.win_x = window.x
            self.win_y = window.y
            self._write_config()
        except Exception:
            pass

    def list_models(self, api_key=""):
        """拉取中转站支持的模型列表，供设置弹窗自动获取模型 ID。
        Key 留空时沿用已保存的 Key（页面出于安全不回填明文 Key）。"""
        api_key = (api_key or '').strip() or self.key
        if not api_key:
            return {'ok': False, 'msg': '请先填写 API Key'}
        try:
            client = OpenAI(api_key=api_key, base_url=self.base_url)
            resp = client.models.list()
            ids = sorted([m.id for m in resp.data])
            return {'ok': True, 'models': ids}
        except Exception as e:
            msg = str(e)
            if len(msg) > 260:
                msg = msg[:260] + '...'
            return {'ok': False, 'msg': '获取模型列表失败：' + msg}

    def save_theme(self, theme):
        """单独持久化主题，避免和接口设置耦合"""
        self.theme = 'light' if theme == 'light' else 'dark'
        try:
            self._write_config()
        except Exception as e:
            return {'success': False, 'error': str(e)}
        return {'success': True}

    def test_connection(self, api_key, model):
        """保存前测试连接：验证 Key 是否可用，并检查模型名是否在中转站的模型列表里。
        Key 留空时沿用已保存的 Key（页面出于安全不回填明文 Key）。"""
        api_key = (api_key or '').strip() or self.key
        model = (model or '').strip()
        if not api_key:
            return {'ok': False, 'msg': '请先填写 API Key'}
        if not model:
            return {'ok': False, 'msg': '请先选择模型'}
        try:
            client = OpenAI(api_key=api_key, base_url=self.base_url)
            resp = client.models.list()
            ids = []
            try:
                ids = [m.id for m in resp.data]
            except Exception:
                ids = []
            if ids and model not in ids:
                # 有些中转站不在 /models 里列出图片模型，属正常，仅提醒而非失败
                return {'ok': True, 'warn': True,
                        'msg': '连接成功，但模型列表中未找到 "' + model + '"，若确认名称正确可忽略'}
            return {'ok': True, 'warn': False, 'msg': '连接成功，配置可用'}
        except Exception as e:
            msg = str(e)
            if len(msg) > 260:
                msg = msg[:260] + '...'
            return {'ok': False, 'msg': '连接失败：' + msg}

    def _js(self, expr):
        """把一段 JS 推回页面执行；窗口已关闭等异常时静默忽略。"""
        try:
            webview.windows[0].evaluate_js(expr)
        except Exception:
            pass

    def _push_item(self, gen_id, entry_id, index, total):
        """只通知前端"有一张图生成好了"，不把图片数据拼进 JS 字符串。
        前端收到通知后通过 get_history_item 走标准的 JS-API 桥获取图片本体，
        避免大体量 base64 直接拼接进 evaluate_js 造成的潜在卡顿/字符串问题。"""
        self._js(
            '__onGenItem(' +
            json.dumps(gen_id, ensure_ascii=False) + ',' +
            json.dumps(entry_id, ensure_ascii=False) + ',' +
            str(index) + ',' + str(total) + ')'
        )

    def _sanitize_count(self, count):
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 1
        return max(1, min(4, count))

    def edit_image(self, prompt="", ref_data_urls=None, size="1024x1024", quality="auto", count=1, gen_id=""):
        """图生图/带参考图生成：prompt/参考图都作为参数直接传入，支持多个批次并发进行"""
        self._start_batch(prompt, ref_data_urls or [], size, quality, count, gen_id, is_edit=True)

    def generate(self, prompt="", size="1024x1024", quality="auto", count=1, gen_id=""):
        """文生图：支持与其他批次并发进行"""
        self._start_batch(prompt, [], size, quality, count, gen_id, is_edit=False)

    def _start_batch(self, prompt, ref_data_urls, size, quality, count, gen_id, is_edit):
        if not self.key:
            self._js('__onGenError(' + json.dumps(gen_id, ensure_ascii=False) +
                     ', "请先在设置中配置 API Key")')
            return
        count = self._sanitize_count(count)

        def worker(prompt_inner, ref_data_urls_inner, size_inner, quality_inner, count_inner):
            try:
                ref_bytes_list = []
                if is_edit:
                    if not ref_data_urls_inner:
                        self._js('__onGenError(' + json.dumps(gen_id, ensure_ascii=False) +
                                 ', "未获取到参考图")')
                        return
                    for data_url in ref_data_urls_inner:
                        b64_part = data_url.split(',', 1)[1] if ',' in data_url else data_url
                        ref_bytes_list.append(base64.b64decode(b64_part))
                elif not prompt_inner:
                    self._js('__onGenError(' + json.dumps(gen_id, ensure_ascii=False) +
                             ', "未获取到提示词")')
                    return

                client = OpenAI(api_key=self.key, base_url=self.base_url)
                got = 0
                for _ in range(count_inner):
                    try:
                        b64 = self._call_image_api(
                            client, prompt_inner, ref_bytes_list, size_inner, quality_inner, is_edit
                        )
                    except Exception as e:
                        # 已产出部分结果时不整批报错，仅提示这一张失败
                        if got > 0:
                            self._js('__onGenPartial(' + json.dumps(gen_id, ensure_ascii=False) + ')')
                            break
                        raise e
                    entry_id = self._save_history_entry(
                        prompt_inner, b64, size_inner, quality_inner,
                        ref_bytes_list if is_edit else None
                    )
                    got += 1
                    self._push_item(gen_id, entry_id, got, count_inner)

                self._js('__onGenDone(' + json.dumps(gen_id, ensure_ascii=False) +
                         ',' + str(got) + ')')
            except Exception as e:
                error_msg = str(e)
                if len(error_msg) > 300:
                    error_msg = error_msg[:300] + '...'
                self._js('__onGenError(' + json.dumps(gen_id, ensure_ascii=False) + ',' +
                         json.dumps(error_msg, ensure_ascii=False) + ')')

        # 立即开启子线程，主函数瞬间返回，UI 永远不会卡；多个批次的线程互相独立，可以同时进行
        threading.Thread(
            target=worker, args=(prompt, ref_data_urls, size, quality, count), daemon=True
        ).start()

    def _call_image_api(self, client, prompt, ref_bytes_list, size, quality, is_edit):
        """执行单次图片 API 调用，返回 b64 字符串。"""
        if is_edit:
            image_files = [
                (f'ref_{i}.png', io.BytesIO(data), 'image/png')
                for i, data in enumerate(ref_bytes_list)
            ]
            kwargs = dict(
                model=self.model,
                image=image_files,
                prompt=prompt or "",
                n=1,
                size=size,
                response_format="b64_json"
            )
            if quality and quality != "auto":
                kwargs['quality'] = quality
            response = client.images.edit(**kwargs)
        else:
            kwargs = dict(
                model=self.model,
                prompt=prompt,
                n=1,
                size=size,
                response_format="b64_json"
            )
            if quality and quality != "auto":
                kwargs['quality'] = quality
            response = client.images.generate(**kwargs)
        return response.data[0].b64_json

    def _load_history_entries(self):
        if os.path.exists(self.history_path):
            try:
                with open(self.history_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_history_entries(self, entries):
        with open(self.history_path, 'w', encoding='utf-8') as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

    def _read_b64(self, filename):
        """按文件名读取历史目录下的文件并转 base64；文件不存在/已删除时返回 None"""
        if not filename:
            return None
        try:
            with open(os.path.join(self.history_dir, filename), 'rb') as f:
                return base64.b64encode(f.read()).decode('ascii')
        except Exception:
            return None

    def _make_thumbnail(self, image_bytes):
        """生成侧边栏用的小尺寸缩略图（JPEG，体积远小于原图），失败时返回 None 由调用方回退"""
        try:
            img = Image.open(io.BytesIO(image_bytes))
            img.thumbnail((THUMB_MAX_SIZE, THUMB_MAX_SIZE), Image.Resampling.LANCZOS)
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=82)
            return buf.getvalue()
        except Exception:
            return None

    def _delete_entry_files(self, entry):
        """删除某条历史记录对应的原图/缩略图/参考图文件（找不到时静默跳过）"""
        filenames = [entry.get('filename', ''), entry.get('thumb_filename', '')]
        filenames.extend(entry.get('ref_filenames', []))
        for name in filenames:
            if not name:
                continue
            try:
                os.remove(os.path.join(self.history_dir, name))
            except OSError:
                pass

    def _trim_history_entries(self, entries):
        """历史记录超过上限时，删除最旧记录的文件并从列表中移除，避免历史无限增长拖慢启动"""
        if len(entries) <= MAX_HISTORY_ENTRIES:
            return entries
        keep = entries[:MAX_HISTORY_ENTRIES]
        for entry in entries[MAX_HISTORY_ENTRIES:]:
            self._delete_entry_files(entry)
        return keep

    def _save_history_entry(self, prompt, b64, size, quality, ref_bytes_list=None):
        entry_id = uuid.uuid4().hex
        filename = entry_id + '.png'
        image_bytes = base64.b64decode(b64)
        with open(os.path.join(self.history_dir, filename), 'wb') as f:
            f.write(image_bytes)

        thumb_filename = ''
        thumb_bytes = self._make_thumbnail(image_bytes)
        if thumb_bytes is not None:
            thumb_filename = entry_id + '_thumb.jpg'
            with open(os.path.join(self.history_dir, thumb_filename), 'wb') as f:
                f.write(thumb_bytes)

        ref_filenames = []
        for i, ref_bytes in enumerate(ref_bytes_list or []):
            ref_filename = entry_id + '_ref' + str(i) + '.png'
            with open(os.path.join(self.history_dir, ref_filename), 'wb') as f:
                f.write(ref_bytes)
            ref_filenames.append(ref_filename)

        entries = self._load_history_entries()
        entries.insert(0, {
            'id': entry_id,
            'prompt': prompt,
            'filename': filename,
            'thumb_filename': thumb_filename,
            'ref_filenames': ref_filenames,
            'size': size,
            'quality': quality,
            'created_at': time.time()
        })
        entries = self._trim_history_entries(entries)
        self._save_history_entries(entries)
        return entry_id

    def get_history(self):
        """读取历史记录列表，仅返回缩略图供侧边栏展示。
        原图和参考图体积大，改为用户实际需要查看时（点击历史项/新图生成完成）
        再通过 get_history_item 按需读取，避免启动时一次性加载全部原图拖慢启动、占用内存。"""
        result = []
        for entry in self._load_history_entries():
            thumb_filename = entry.get('thumb_filename', '')
            b64 = self._read_b64(thumb_filename)
            is_thumb = b64 is not None
            if b64 is None:
                # 兼容更新前生成的历史记录（当时还没有缩略图文件），回退读取原图
                b64 = self._read_b64(entry.get('filename', ''))
            if b64 is None:
                continue
            result.append({
                'id': entry.get('id'),
                'prompt': entry.get('prompt', ''),
                'thumbnail': b64,
                'thumb_mime': 'image/jpeg' if is_thumb else 'image/png'
            })
        return result

    def get_history_item(self, entry_id):
        """按需读取单条历史记录的原图 + 参考图（用于查看大图/还原卡片/新图推送展示）"""
        entry = next((e for e in self._load_history_entries() if e.get('id') == entry_id), None)
        if entry is None:
            return {'ok': False, 'msg': '记录不存在或已被删除'}
        b64 = self._read_b64(entry.get('filename', ''))
        if b64 is None:
            return {'ok': False, 'msg': '图片文件缺失'}
        thumb_filename = entry.get('thumb_filename', '')
        thumb_b64 = self._read_b64(thumb_filename)
        is_thumb = thumb_b64 is not None
        if thumb_b64 is None:
            thumb_b64 = b64
        ref_images = []
        for ref_filename in entry.get('ref_filenames', []):
            ref_b64 = self._read_b64(ref_filename)
            if ref_b64 is not None:
                ref_images.append(ref_b64)
        return {
            'ok': True,
            'id': entry.get('id'),
            'prompt': entry.get('prompt', ''),
            'size': entry.get('size', ''),
            'quality': entry.get('quality', ''),
            'image': b64,
            'thumbnail': thumb_b64,
            'thumb_mime': 'image/jpeg' if is_thumb else 'image/png',
            'ref_images': ref_images
        }

    def delete_history_item(self, entry_id):
        entries = self._load_history_entries()
        removed = None
        remaining = []
        for entry in entries:
            if entry.get('id') == entry_id:
                removed = entry
            else:
                remaining.append(entry)
        if removed:
            self._delete_entry_files(removed)
        self._save_history_entries(remaining)
        return {'success': True}

    def save_image(self, b64_data):
        """弹出系统保存对话框，让用户选择图片保存路径"""
        default_name = 'ai-{}.png'.format(int(time.time()))
        result = webview.windows[0].create_file_dialog(
            FileDialog.SAVE,
            save_filename=default_name,
            file_types=('PNG 图片 (*.png)', 'All files (*.*)')
        )
        if not result:
            return {'success': False, 'canceled': True}

        path = result if isinstance(result, str) else result[0]
        try:
            with open(path, 'wb') as f:
                f.write(base64.b64decode(b64_data))
            return {'success': True, 'path': path}
        except Exception as e:
            return {'success': False, 'error': str(e)}


def _get_icon_path():
    # Windows 用 .ico，macOS 的 pywebview(NSImage) 不认 ico，用 .png/.icns
    icon_name = 'app.icns' if sys.platform == 'darwin' else 'app.ico'
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'icon', icon_name)
    else:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon', icon_name)


if __name__ == '__main__':
    api = Api()
    html_path = api._get_html_path()
    min_w, min_h = 760, 640
    window = webview.create_window(
        '梯度云·AI生图 - TDYUN AI Art',
        html_path,
        js_api=api,
        width=max(api.win_width, min_w),
        height=max(api.win_height, min_h),
        x=api.win_x,
        y=api.win_y,
        min_size=(min_w, min_h),
        text_select=True
    )
    window.events.closing += api._save_window_geometry
    webview.start(icon=_get_icon_path())