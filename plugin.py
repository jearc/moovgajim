from threading import Thread
import re
from functools import reduce
from pathlib import Path
import time
from gajim.common import app
from gajim.common import ged
from gajim.plugins import GajimPlugin
from gajim.plugins.plugins_i18n import _
from gajim.common.structs import OutgoingMessage
from gajim.common import configpaths
from gi.repository import GLib
from nbxmpp.modules.misc import build_xhtml_body
import moovgajim.moov as moov
import moovgajim.moovdb as moovdb


def parse_time(string):
	ns = re.findall(r'-?\d+', string)
	return reduce(lambda t, n: 60*t + int(n), ns[:3], 0)


def parse_set(string):
	parts = string.split()
	return {
		'playlist_position': int(parts[0]) - 1,
		'paused':  parts[1] == 'paused',
		'time':  parse_time(parts[2])
	}


def format_time(time):
	s = int(round(time))
	h, s = s // 3600, s % 3600
	m, s = s // 60, s % 60
	return (f'{h}:{m:02}' if h else f'{m}') + f':{s:02}'


def format_status(status):
	s = f'{status["playlist_position"]+1}/{status["playlist_count"]} '
	s += 'paused' if status['paused'] else 'playing'
	s += f' {format_time(status["time"])}'
	return s


class Conversation:

	def __init__(self, account, contact, conn):
		self._account = account
		self._contact = contact
		self._conn = conn

	def send(self, text, xhtml=None):
		if xhtml is not None:
			xhtml = build_xhtml_body(xhtml)
		def f(text):
			message = OutgoingMessage(self._account, self._contact, text, 'chat', xhtml=xhtml)
			self._conn.send_message(message)
		GLib.idle_add(f, text)


class MoovPlugin(GajimPlugin):

	moov_thread = None
	moov = None
	conv = None
	video_url = None
	db = None

	def init(self):
		self.description = _('Adds Moov support to Gajim')
		self.config_dialog = None
		self.events_handlers = {
			'decrypted-message-received': (ged.PREGUI, self._on_message_received),
			'message-sent': (ged.PREGUI, self._on_message_sent),
		}
		db_path = Path(configpaths.get('PLUGINS_DATA')) / 'moov' / 'db.json'
		self.db = moovdb.MoovDB(db_path)

	def _on_message_received(self, event):
		if not event.msgtxt:
			return

		contact = app.contacts.get_contact(event.account, event.jid)
		conv = Conversation(event.account, contact, event.conn)
		self.relay_message(event.msgtxt, False)
		self.handle_command(conv, event.msgtxt)

	def _on_message_sent(self, event):
		if not event.message:
			return
		if not event.control:
			return

		conv = Conversation(
			event.control.account,
			event.control.contact,
			event.control.connection
		)

		self.relay_message(event.message, True)
		self.handle_command(conv, event.message)

	def send_message(self, body):
		def f(body):
			self.conv.send(body)
			self.relay_message(body, True)
			self.handle_command(self.conv, body)
		GLib.idle_add(f, body)

	def handle_command(self, conv, message):
		tokens = message.split()

		alive = self.moov is not None and self.moov.alive()

		if tokens[0] == '.status':
			if alive:
				self.send_message(format_status(self.moov.get_status()))
			else:
				conv.send('nothing playing')
		elif tokens[0] == 'pp':
			if alive:
				self.moov.toggle_paused()
				self.send_message(format_status(self.moov.get_status()))
				self.update_db()
		elif message[0:6] == '.seek ':
			if alive:
				self.moov.seek(parse_time(message[6:]))
				self.send_message(format_status(self.moov.get_status()))
				self.update_db()
		elif message[0:7] == '.seek+ ':
			if alive:
				self.moov.relative_seek(parse_time(message[7:]))
				self.send_message(format_status(self.moov.get_status()))
				self.update_db()
		elif message[0:7] == '.seek- ':
			if alive:
				self.moov.relative_seek(-parse_time(message[7:]))
				self.send_message(format_status(self.moov.get_status()))
				self.update_db()
		elif message[0:5] == '.set ':
			if alive:
				try:
					args = parse_set(message[5:])
					self.moov.set_canonical(args['playlist_position'], args['paused'], args['time'])
					self.send_message(format_status(self.moov.get_status()))
					self.update_db()
				except:
					conv.send('error: invalid args')
		elif tokens[0] == '.close':
			if alive:
				self.update_db()
				self.kill_moov()
		elif tokens[0] == '.add':
			if self.db is not None:
				try:
					url = tokens[1]
					time = 0 if len(tokens) < 3 else parse_time(tokens[2])
				except:
					conv.send('error: invalid args')

				def cb(info):
					(index, session, dupe) = self.db.add(info, time)
					prefix = 'already have ' if dupe else 'added '
					text = prefix + moovdb.format_session_text(index, session)
					xhtml = prefix + moovdb.format_session_html(index, session)
					conv.send(text, xhtml=xhtml)

				download_thread = Thread(target=self.download_info, args=[url, cb, conv])
				download_thread.start()
		elif tokens[0] == '.o':
			try:
				url = tokens[1]
				time = 0 if len(tokens) < 3 else parse_time(tokens[2])
			except:
				conv.send('error: invalid args')

			def cb(info):
				if self.db is not None:
					(index, session, dupe) = self.db.add(info, time)
					self.db.set_top(index)
				self.video_url = info['url']
				self.conv = conv
				self.open_moov()
				self.moov.append(self.video_url)
				self.moov.seek(time)
				self.send_message(format_status(self.moov.get_status()))

			download_thread = Thread(target=self.download_info, args=[url, cb, conv])
			download_thread.start()
		elif tokens[0] == '.list':
			if self.db is not None:
				session_list = self.db.list()
				if len(session_list) != 0:
					text = moovdb.format_sessions_text(self.db.list())
					xhtml = moovdb.format_sessions_html(self.db.list())
					conv.send(text, xhtml=xhtml)
				else:
					conv.send('no sessions')
		elif tokens[0] == '.pop':
			if self.db is not None:
				indices = tokens[1:]
				for i in range(len(indices)):
					indices[i] = int(indices[i])
				self.db.pop(indices)
				text = moovdb.format_sessions_text(self.db_list())
				xhtml = moovdb.format_sessions_html(self.db.list())
				conv.send(text, xhtml=xhtml)
		elif tokens[0] == '.resume':
			if self.db is not None:
				if len(tokens) >= 2:
					try:
						self.db.set_top(int(tokens[1]))
					except:
						return
				session = self.db.top()
				self.conv = conv
				self.open_moov()
				self.video_url = session['video_info']['url']
				self.moov.append(self.video_url)
				self.moov.seek(session['time'])
				self.conv.send(f'.o {self.video_url} {format_time(session["time"])}')
				self.send_message(format_status(self.moov.get_status()))
		elif tokens[0] == '.re':
			if self.db is not None and alive:
				time_str = format_time(self.moov.get_status()['time'])
				self.conv.send(f'.o {self.video_url} {time_str}')


	def download_info(self, url, callback, conv):
		try:
			info = moovdb.download_info(url)
			GLib.idle_add(callback, info)
		except:
			GLib.idle_add(conv.send, 'error: could not get video information')

	def handle_control(self, control_command):
		p = control_command['playlist_position'] + 1
		t = format_time(control_command['time'])
		pp = 'paused' if control_command['paused'] else 'playing'
		message = f'.set {p} {pp} {t}'
		self.conv.send(message)

	def open_moov(self):
		self.kill_moov()
		self.moov = moov.Moov()
		self.moov_thread = Thread(target=self.moov_thread_f)
		self.moov_thread.start()

	def update_db(self):
		if self.db is not None:
			time = self.moov.get_status()['time']
			self.db.update_time(self.video_url, time)

	def moov_thread_f(self):
		last_update = time.time()
		while self.moov is not None and self.moov.alive():
			now = time.time()
			if now - last_update > 5:
				GLib.idle_add(self.update_db)
				last_update = now
			for user_input in self.moov.get_user_inputs():
				GLib.idle_add(self.send_message, user_input)
			for control_command in self.moov.get_user_control_commands():
				GLib.idle_add(self.handle_control, control_command)
			time.sleep(0.01)
		if self.moov is not None:
			self.kill_moov()

	def relay_message(self, message, own):
		if self.moov and self.moov.alive():
			fg = '#ffffbf' if own else '#afeeee'
			self.moov.put_message(message, fg, "#00000088")

	def kill_moov(self):
		if not self.moov:
			return
		self.moov.close()
		self.moov = None
		self.moov_thread = None
