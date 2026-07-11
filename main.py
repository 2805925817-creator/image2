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

# 固定的中转站地址，不允许用户修改
BASE_URL = "https://tdyun.ai/v1"


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
        # 生成任务的取消标记：记录已被用户取消的 gen_id
        self._canceled = set()
        self._cancel_lock = threading.Lock()
        self._load_config()

    def _get_config_path(self):
        if getattr(sys, 'frozen', False):
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
            except Exception:
                pass

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
                'base_url': self.base_url,
                'api_key': self.key,
                'model': self.model,
                'theme': self.theme
            }, f, ensure_ascii=False, indent=2)

    def save_config(self, api_key, model):
        self.key = api_key
        self.model = model
        self._write_config()
        return {'success': True}

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
        self._write_config()
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

    def cancel_generation(self, gen_id):
        """标记某个生成任务为已取消。已发出的 API 请求无法真正中断，
        但结果返回后会被丢弃，不再回填到页面。"""
        if gen_id:
            with self._cancel_lock:
                self._canceled.add(gen_id)
        return {'success': True}

    def _is_canceled(self, gen_id):
        with self._cancel_lock:
            return gen_id in self._canceled

    def _clear_canceled(self, gen_id):
        with self._cancel_lock:
            self._canceled.discard(gen_id)

    def _js(self, expr):
        """把一段 JS 推回页面执行；窗口已关闭等异常时静默忽略。"""
        try:
            webview.windows[0].evaluate_js(expr)
        except Exception:
            pass

    def _push_item(self, gen_id, b64, entry_id, index, total):
        self._js(
            '__onGenItem(' +
            json.dumps(gen_id, ensure_ascii=False) + ',' +
            json.dumps(b64, ensure_ascii=False) + ',' +
            json.dumps(entry_id, ensure_ascii=False) + ',' +
            str(index) + ',' + str(total) + ')'
        )

    def _sanitize_count(self, count):
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 1
        return max(1, min(4, count))

    def edit_image(self, size="1024x1024", quality="auto", count=1, gen_id=""):
        """图生图/带参考图生成：提示词和参考图（data URL 列表）都从页面 JS 变量里读取，避免长字符串作为调用参数传递"""
        self._start_batch(size, quality, count, gen_id, is_edit=True)

    def generate(self, size="1024x1024", quality="auto", count=1, gen_id=""):
        """文生图：不在参数里传长文本，改为从 JS 变量里读取，防止窗口卡死"""
        self._start_batch(size, quality, count, gen_id, is_edit=False)

    def _start_batch(self, size, quality, count, gen_id, is_edit):
        if not self.key:
            self._js('__onGenError(' + json.dumps(gen_id, ensure_ascii=False) +
                     ', "请先在设置中配置 API Key")')
            return
        count = self._sanitize_count(count)

        def worker(size_inner, quality_inner, count_inner):
            try:
                prompt = webview.windows[0].evaluate_js('window.__currentPrompt')
                ref_bytes_list = []
                if is_edit:
                    ref_data_urls = webview.windows[0].evaluate_js('window.__currentRefImgs') or []
                    if not ref_data_urls:
                        self._js('__onGenError(' + json.dumps(gen_id, ensure_ascii=False) +
                                 ', "未获取到参考图")')
                        return
                    for data_url in ref_data_urls:
                        b64_part = data_url.split(',', 1)[1] if ',' in data_url else data_url
                        ref_bytes_list.append(base64.b64decode(b64_part))
                elif not prompt:
                    self._js('__onGenError(' + json.dumps(gen_id, ensure_ascii=False) +
                             ', "未获取到提示词")')
                    return

                client = OpenAI(api_key=self.key, base_url=self.base_url)
                got = 0
                for _ in range(count_inner):
                    if self._is_canceled(gen_id):
                        break
                    try:
                        b64 = self._call_image_api(
                            client, prompt, ref_bytes_list, size_inner, quality_inner, is_edit
                        )
                    except Exception as e:
                        # 已产出部分结果时不整批报错，仅提示这一张失败
                        if got > 0:
                            self._js('__onGenPartial(' + json.dumps(gen_id, ensure_ascii=False) + ')')
                            break
                        raise e
                    # 请求返回后再查一次取消状态，取消则丢弃结果不落库
                    if self._is_canceled(gen_id):
                        break
                    entry_id = self._save_history_entry(
                        prompt, b64, size_inner, quality_inner,
                        ref_bytes_list if is_edit else None
                    )
                    got += 1
                    self._push_item(gen_id, b64, entry_id, got, count_inner)

                if self._is_canceled(gen_id):
                    self._js('__onGenCanceled(' + json.dumps(gen_id, ensure_ascii=False) + ')')
                else:
                    self._js('__onGenDone(' + json.dumps(gen_id, ensure_ascii=False) +
                             ',' + str(got) + ')')
            except Exception as e:
                error_msg = str(e)
                if len(error_msg) > 300:
                    error_msg = error_msg[:300] + '...'
                self._js('__onGenError(' + json.dumps(gen_id, ensure_ascii=False) + ',' +
                         json.dumps(error_msg, ensure_ascii=False) + ')')
            finally:
                self._clear_canceled(gen_id)

        # 立即开启子线程，主函数瞬间返回，UI 永远不会卡
        threading.Thread(target=worker, args=(size, quality, count), daemon=True).start()

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

    def _save_history_entry(self, prompt, b64, size, quality, ref_bytes_list=None):
        entry_id = uuid.uuid4().hex
        filename = entry_id + '.png'
        with open(os.path.join(self.history_dir, filename), 'wb') as f:
            f.write(base64.b64decode(b64))

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
            'ref_filenames': ref_filenames,
            'size': size,
            'quality': quality,
            'created_at': time.time()
        })
        self._save_history_entries(entries)
        return entry_id

    def get_history(self):
        """读取本地保存的历史记录，返回给前端在侧边栏展示"""
        result = []
        for entry in self._load_history_entries():
            filepath = os.path.join(self.history_dir, entry.get('filename', ''))
            try:
                with open(filepath, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode('ascii')
            except Exception:
                continue
            ref_images = []
            for ref_filename in entry.get('ref_filenames', []):
                try:
                    with open(os.path.join(self.history_dir, ref_filename), 'rb') as f:
                        ref_images.append(base64.b64encode(f.read()).decode('ascii'))
                except Exception:
                    pass
            result.append({
                'id': entry.get('id'),
                'prompt': entry.get('prompt', ''),
                'size': entry.get('size', ''),
                'quality': entry.get('quality', ''),
                'image': b64,
                'ref_images': ref_images
            })
        return result

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
            try:
                os.remove(os.path.join(self.history_dir, removed.get('filename', '')))
            except OSError:
                pass
            for ref_filename in removed.get('ref_filenames', []):
                try:
                    os.remove(os.path.join(self.history_dir, ref_filename))
                except OSError:
                    pass
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


if __name__ == '__main__':
    api = Api()
    html_path = api._get_html_path()
    window = webview.create_window(
        '梯度云·AI生图 - TDYUN AI Art',
        html_path,
        js_api=api,
        width=1040,
        height=920,
        min_size=(760, 640),
        text_select=True
    )
    webview.start()