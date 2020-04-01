###
# Copyright (c) 2015, Moritz Lipp
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

import json

from supybot.commands import wrap
import supybot.ircdb as ircdb
import supybot.ircmsgs as ircmsgs
import supybot.callbacks as callbacks
import supybot.log as log
import supybot.httpserver as httpserver
import supybot.world as world
try:
    from supybot.i18n import PluginInternationalization
    from supybot.i18n import internationalizeDocstring
    _ = PluginInternationalization('Wekan')
except ImportError:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    def _(x):
        return x

    def internationalizeDocstring(x):
        return x


class WekanHandler(object):

    """Handle Wekan messages"""

    def __init__(self, plugin):
        # HACK: instead of refactoring everything, I can just replace this with each handle_payload() call.
        self.irc = None
        self.plugin = plugin
        self.log = log.getPluginLogger('Wekan')

    def handle_payload(self, headers, payload, irc):
        self.irc = irc
        self.log.debug('Gogs: running on network %r', irc.network)

        text, board_url = (payload['text']).split('\n')
        # Check if any channel has subscribed to this project
        for channel in irc.state.channels.keys():
            boards = self.plugin._load_boards(channel)
            for slug, url in boards.items():
                print(board_url.startswith(url))
                # Parse board url
                if not board_url.startswith(url):
                    continue

                # Just send payload['text']
                self._send_message(channel, text)

    def _send_message(self, channel, msg):
        if self.plugin.registryValue('use-notices', channel):
            announce_msg = ircmsgs.notice(channel, msg)
        else:
            announce_msg = ircmsgs.privmsg(channel, msg)
        self.irc.queueMsg(announce_msg)


class WekanWebHookService(httpserver.SupyHTTPServerCallback):
    """https://github.com/wekan/wekan/wiki/Webhook-data"""

    name = "WekanWebHookService"
    defaultResponse = """This plugin handles only POST request, please don't use other requests."""

    def __init__(self, plugin):
        self.log = log.getPluginLogger('Wekan')
        self.wekan = WekanHandler(plugin)
        self.plugin = plugin

    def _send_error(self, handler, message):
        handler.send_response(403)
        handler.send_header('Content-type', 'text/plain')
        handler.end_headers()
        handler.wfile.write(message.encode('utf-8'))

    def _send_ok(self, handler):
        handler.send_response(200)
        handler.send_header('Content-type', 'text/plain')
        handler.end_headers()
        handler.wfile.write(bytes('OK', 'utf-8'))

    def doPost(self, handler, path, form):
        headers = dict(self.headers)

        network = None

        try:
            information = path.split('/')[1:]
            network = information[0]
        except IndexError:
            self._send_error(handler, _("""Error: You need to provide the
                                        network name in the URL."""))
            return
        
        irc = world.getIrc(network)
        if irc is None:
            self._send_error(handler, (_('Error: Unknown network %r') % network))
            return

        # Handle payload
        payload = None
        try:
            payload = json.JSONDecoder().decode(form.decode('utf-8'))
        except Exception as e:
            self.log.info(e)
            self._send_error(handler, _('Error: Invalid JSON data sent.'))
            return

        try:
            self.wekan.handle_payload(headers, payload, irc)
        except Exception as e:
            self.log.info(e)
            self._send_error(handler, _('Error: Invalid data sent.'))
            return

        # Return OK
        self._send_ok(handler)


class Wekan(callbacks.Plugin):
    """Plugin for communication and notifications of a Wekan boards
    management tool instance"""
    threaded = True

    def __init__(self, irc):
        global instance
        self.__parent = super(Wekan, self)
        self.__parent.__init__(irc)
        instance = self

        callback = WekanWebHookService(self)
        httpserver.hook('wekan', callback)

    def die(self):
        httpserver.unhook('wekan')
        self.__parent.die()

    def _load_boards(self, channel):
        boards = self.registryValue('boards', channel)
        if boards is None:
            return {}
        else:
            return boards

    def _save_boards(self, boards, channel):
        self.setRegistryValue('boards', value=boards, channel=channel)

    def _check_capability(self, irc, msg):
        if ircdb.checkCapability(msg.prefix, 'admin'):
            return True
        else:
            irc.errorNoCapability('admin')
            return False

    class wekan(callbacks.Commands):
        """Wekan commands"""

        class board(callbacks.Commands):
            """Board commands"""

            @internationalizeDocstring
            def add(self, irc, msg, args, channel, board_slug, board_url):
                """[<channel>] <board-slug> <board-url>

                Announces the changes of the board with the slug
                <board-slug> and the url <board-url> to <channel>.
                """
                if not instance._check_capability(irc, msg):
                    return

                boards = instance._load_boards(channel)
                if board_slug in boards:
                    irc.error(
                        _('This board is already announced to this channel.'))
                    return

                # Save new board mapping
                boards[board_slug] = board_url
                instance._save_boards(boards, channel)

                irc.replySuccess()

            add = wrap(add, ['channel', 'somethingWithoutSpaces', 'httpUrl'])

            @internationalizeDocstring
            def remove(self, irc, msg, args, channel, board_slug):
                """[<channel>] <board-slug>

                Stops announcing the changes of the board slug <board-slug>
                to <channel>.
                """
                if not instance._check_capability(irc, msg):
                    return

                boards = instance._load_boards(channel)
                if board_slug not in boards:
                    irc.error(
                        _('This board is not registered to this channel.'))
                    return

                # Remove board mapping
                del boards[board_slug]
                instance._save_boards(boards, channel)

                irc.replySuccess()

            remove = wrap(remove, ['channel', 'somethingWithoutSpaces'])

            @internationalizeDocstring
            def list(self, irc, msg, args, channel):
                """[<channel>]

                Lists the registered boards in <channel>.
                """
                if not instance._check_capability(irc, msg):
                    return

                boards = instance._load_boards(channel)
                if boards is None or len(boards) == 0:
                    irc.error(_('This channel has no registered boards.'))
                    return

                for board_slug, board_url in boards.items():
                    irc.reply("%s: %s" % (board_slug, board_url))

            list = wrap(list, ['channel'])


Class = Wekan

