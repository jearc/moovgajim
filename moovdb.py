import subprocess
import json
import html
import os.path
import os

def download_info(url):
    p = subprocess.run(['youtube-dl', '-j', url], capture_output=True, encoding='utf-8')
    j = json.loads(p.stdout)
    return {
        'url': url,
        'title': j['title'],
        'uploader': j['uploader'] if 'uploader' in j else 'Unknown',
        'uploader_url': j['uploader_url'] if 'uploader_url' in j else None,
        'duration': j['duration'] if 'duration' in j else None
    }

def format_time(time):
	s = int(round(time))
	h, s = s // 3600, s % 3600
	m, s = s // 60, s % 60
	return (f'{h}:{m:02}' if h else f'{m}') + f':{s:02}'

def format_link(url, text):
    return f'<a href="{url}">{html.escape(text)}</a>'

def format_session_html(index, session):
    info = session['video_info']
    index = html.escape(f'[{index}]')
    uploader = html.escape(info['uploader'])
    if info['uploader_url'] is not None:
        uploader = format_link(info['uploader_url'], info['uploader'])
    link = format_link(info['url'], info['title'])
    time = html.escape(format_time(session["time"]))
    return f'{index} {uploader}: {link} {time}'

def format_session_text(index, session):
    info = session['video_info']
    index = f'[{index}]'
    uploader = info['uploader']
    title = info['title']
    time = format_time(session['time'])
    return f'{index} {uploader}: {title} {time}'

def format_sessions_html(sessions):
    res = ''
    for i, s in enumerate(sessions):
        res += f'{format_session_html(i, s)} '
    return res

def format_sessions_text(sessions):
    res = ''
    for i, s in enumerate(sessions):
        res += f'{format_session_text(i, s)} '
    return res

class MoovDB:

    _db = []

    def __init__(self, save_path):
        self._save_path = save_path
        self._load(save_path)

    def _load(self, save_path):
        if os.path.isfile(save_path):
            with open(save_path, 'r') as fp:
                self._db = json.load(fp)

    def _save(self):
        os.makedirs(os.path.dirname(self._save_path), exist_ok=True)
        with open(self._save_path, 'w+') as fp:
            json.dump(self._db, fp, indent=4)

    def list(self):
        return self._db

    def add(self, video_info, time):
        for i, s in enumerate(self._db):
            if s['video_info']['url'] == video_info['url']:
                return (i, s, True)
        self._db.append({'video_info': video_info, 'time': time})
        self._save()
        return (len(self._db) - 1, self.top(), False)

    def set_top(self, index):
        self._db.append(self._db.pop(index))
        self._save()
        return self.top()

    def index_of_url(self, url):
        for i, s in enumerate(self._db):
            if s['video_info']['url'] == url:
                return i
        return None

    def update_time(self, url, time):
        index = self.index_of_url(url)
        if index is not None:
            self._db[index]['time'] = time
            self._save()

    def top(self):
        return self._db[-1]

    def pop(self, indices):
        if len(indices) == 0:
            if len(self._db) != 0:
                del self._db[-1]
        else:
            for index in sorted(indices, reverse=True):
                if 0 <= index < len(self._db):
                    del self._db[index]
        self._save()
