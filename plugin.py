from threading import Thread
from subprocess import Popen, PIPE
from gajim.common import app
from gajim.common import ged
from gajim.plugins import GajimPlugin
from gajim.plugins.plugins_i18n import _
from gajim.common.structs import OutgoingMessage
from gi.repository import GLib


class MoovPlugin(GajimPlugin):

	moov_thread = None
	moov_proc = None
	account = None
	conn = None
	contact = None

	def init(self):
		self.description = _('Adds Moov support to Gajim')
		self.config_dialog = None
		self.events_handlers = {
			'decrypted-message-received': (ged.PREGUI, self._on_message_received),
			'message-sent': (ged.PREGUI, self._on_message_sent),
		}

	def _on_message_received(self, event):
		if not event.msgtxt:
			return
		
		contact = app.contacts.get_contact(event.account, event.jid)
		self.relay_message(contact.get_shown_name(), event.msgtxt)
		self.handle_command(event.account, event.conn, contact, event.msgtxt)
	
	def _on_message_sent(self, event):
		if not event.message:
			return		
		if not event.control:
			return
			
		self.relay_message(event.control.get_our_nick(), event.message)
		self.handle_command(event.control.account, event.control.connection,
		    event.control.contact, event.message)
	
	def send_message(self, body):
		message = OutgoingMessage(self.account, self.contact, body, 'chat')
		self.conn.send_message(message)
		self.relay_message(app.nicks[self.account], body)
		self.handle_command(self.account, self.conn, self.contact, body)
	
	def handle_command(self, account, conn, contact, message):
		tokens = message.split()
		if tokens[0] == 'YT':
			args = [tokens[1]]
			if len(tokens) > 2:
				args += ['-s', ':'.join(tokens[2:])]
			self.account = account
			self.conn = conn
			self.contact = contact
			self.open_moov(args)
	
	def open_moov(self, args):
		self.kill_moov()
		self.moov_proc = Popen(['moov'] + args,
		    stdin=PIPE, stdout=PIPE, bufsize=1, universal_newlines=True)
		self.moov_thread = Thread(target=self.moov_thread_f)
		self.moov_thread.start()
		
	def moov_thread_f(self):
		partial = ''
		while self.moov_proc and self.moov_proc.poll() is None:
			char = self.moov_proc.stdout.read(1)
			if char == '\0':
				GLib.idle_add(self.send_message, partial)
				partial = ''
			else:
				partial = partial + char
		
	def relay_message(self, nick, message):
		if self.moov_proc and self.moov_proc.poll() is None:
			self.moov_proc.stdin.write(nick + ':' + message + '\0')
			self.moov_proc.stdin.flush()
	
	def kill_moov(self):
		if not self.moov_proc:
			return
		self.moov_proc.terminate()
		self.moov_proc = None
		self.moov_thread = None

