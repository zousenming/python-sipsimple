"""Console control for eventlet apps based on twisted.conch.

Features:
 * blocks keyboard input unless explicitly requested
 * history with arrow keys
 * shortcuts (keys that applications can intercept)
 * "ask question" functionality (hide input prompt, and request
   a key, e.g. accept incoming? [y/n])
 * asynchronously raises EOF exception when Ctrl-D is pressed
   (like Python does for Ctrl-C)
"""
from __future__ import with_statement
import sys
import os
import termios
import tty
import time
from contextlib import contextmanager
from twisted.internet.error import ConnectionDone
from twisted.internet import stdio
from twisted.conch import recvline
from twisted.conch.insults import insults
from eventlet.coros import queue
from eventlet import api
from eventlet.green.thread import allocate_lock

CTRL_C = '\x03'
CTRL_D = '\x04'
CTRL_BACKSLASH = '\x1c'
CTRL_L = '\x0c'

SETTERM_INITIALIZE = '\x1b[!p\x1b[?3;4l\x1b[4l\x1b>'

class EOF(BaseException):
    """user pressed CTRL-D"""

def terminal_initialize(fd):
    # the same effect as calling setterm -initialize
    # QQQ is the sequence is valid for all terminals?
    os.write(fd, '\r' + SETTERM_INITIALIZE)

class ServerProtocol(insults.ServerProtocol):

    def reset(self):
        self.cursorPos.x = self.cursorPos.y = 0
        try:
            del self._savedCursorPos
        except AttributeError:
            pass
        self.write('\n')

class ChannelProxy:

    def __init__(self, source, output):
        self.source = source
        self.output = output
        self.gthread = api.spawn(self._run)
        self.lock = allocate_lock()
        self.exc = None
        self.throw_away = False

    def receive(self):
        return self.output.wait()

    def send(self, x):
        return self.source.send(x)

    def send_exception(self, *args):
        self.exc = args
        return self.source.send_exception(*args)

    def _run(self):
        while True:
            try:
                res = self.source.wait()
            except:
                if not self.throw_away:
                    self.output.send_exception(*sys.exc_info())
            else:
                if not self.throw_away:
                    self.output.send(res)

    def switch_output(self, new_output=None):
        old_output = self.output
        if new_output is None:
            new_output = queue()
        self.output = new_output
        if self.exc is not None:
            self.output.send_exception(*self.exc)
        return old_output

    @contextmanager
    def locked_output(self, new_output=None):
        with self.lock:
            old = self.switch_output(new_output)
            try:
                yield
            finally:
                self.switch_output(old)


class ConsoleProtocol(recvline.HistoricRecvLine):

    channel = ChannelProxy(queue(), queue())
    send_keys = []
    recv_char = False
    last_keypress_time = time.time()
    receiving = 0
    current = api.getcurrent() #### XXX not good, add link_eof function

    ps = ['']
    ps_init = ['']

    def drawInputLine(self, line=None):
        if line is None:
            line= ''.join(self.lineBuffer)
        self.terminal.write(self.ps[self.pn] + line)

    def initializeScreen(self):
        self.terminal.write(self.ps[self.pn])
        self.setInsertMode()

    def connectionMade(self):
        super(ConsoleProtocol, self).connectionMade()
        self.keyHandlers[CTRL_L] = self.handle_FF

    def handle_FF(self):
        self.terminal.eraseDisplay()
        self.terminal.cursorHome()
        self.drawInputLine()

    def _needsNewline(self):
        w = self.terminal.lastWrite
        return not w.endswith('\n') and not w.endswith('\x1bE')

    def addOutput(self, bytes, async=False):
        async = self.receiving>0
        if async:
            self.terminal.eraseLine()
            self.cursorToBOL()

        self.terminal.write(bytes)

        if self._needsNewline():
            self.terminal.nextLine()

        if async:
            self.terminal.write(self.ps[self.pn])

            if self.lineBuffer:
                oldBuffer = self.lineBuffer
                self.lineBuffer = []
                self.lineBufferIndex = 0

                self._deliverBuffer(oldBuffer)

    def _set_ps(self, ps, draw=False):
        if draw:
            self.cursorToBOL()
        self.ps[0] = ps
        if draw:
            self.terminal.eraseLine()
            self.drawInputLine()

    def set_ps(self, ps, draw=False):
        self.ps_init[0] = ps
        self._set_ps(ps, draw)

    def keystrokeReceived(self, keyID, modifier):
        self.last_keypress_time = time.time()
        if self.recv_char or keyID in self.send_keys:
            self.channel.send(('key', (keyID, modifier)))
        elif keyID==CTRL_D:
            api.kill(self.current, EOF())
        elif self.receiving>0:
            super(ConsoleProtocol, self).keystrokeReceived(keyID, modifier)

    def handle_RETURN(self):
        line = ''.join(self.lineBuffer)
        if self.lineBuffer:
            if self.historyLines and self.historyLines[-1]==line:
                pass
            else:
                self.historyLines.append(''.join(self.lineBuffer))
        self.historyPosition = len(self.historyLines)
        self.terminal.eraseLine()
        self.cursorToBOL()
        self.lineBuffer = []
        self.lineBufferIndex = 0
        self.channel.send(('line', line))

    @contextmanager
    def new_prompt(self, new_ps):
        self.terminal.eraseLine()
        self.cursorToBOL()
        lineBuffer = self.lineBuffer[:]
        lineBufferIndex = self.lineBufferIndex
        ps = self.ps[0]
        self.lineBuffer = []
        self.lineBufferIndex = 0
        try:
            self._set_ps(new_ps)
            self.drawInputLine()
            yield
        finally:
            if self._needsNewline():
                self.terminal.nextLine()
            self.lineBuffer = lineBuffer
            self.lineBufferIndex = lineBufferIndex
            self._set_ps(ps)
            self.drawInputLine()

    def barrier(self, seconds=1):
        since_last_keypress = time.time()-self.last_keypress_time
        if since_last_keypress<seconds:
            self.channel.throw_away = True
            try:
                api.sleep(seconds-since_last_keypress)
            finally:
                self.channel.throw_away = False

    def cursorToBOL(self):
        pos = len(self.lineBuffer) + len(self.ps[self.pn])
        if pos>0:
            self.terminal.cursorBackward(pos)

    def clearInputLine(self):
        self.terminal.eraseLine()
        self.cursorToBOL()
        self.lineBuffer = []
        self.drawInputLine()


class GreenConsole:

    last_header = None

    def __init__(self):
        self.writecount = 0

    @property
    def terminalProtocol(self):
        return self.protocol.terminalProtocol

    @property
    def terminal(self):
        return self.protocol.terminalProtocol.terminal

    @property
    def lineBuffer(self):
        return self.terminalProtocol.lineBuffer

    def _receive(self):
        self.terminalProtocol.receiving += 1
        try:
            return self.channel.receive()
        finally:
            self.terminalProtocol.receiving -= 1

    def recv(self):
        if self.terminalProtocol.receiving>=0:
            self.set_ps(self.terminalProtocol.ps_init[0], True)
        try:
            return self._receive()
        finally:
            self.terminal.eraseLine()
            self.terminalProtocol.cursorToBOL()
            self.terminalProtocol._set_ps('', True)

    @contextmanager
    def new_prompt(self, prompt):
        if self.terminalProtocol is None:
            raise ConnectionDone
        if self.terminalProtocol.lineBuffer:
            self.terminalProtocol.terminal.nextLine()
        with self.terminalProtocol.new_prompt(prompt):
            yield

    def recv_char(self, allowed=[], barrier=1):
        if self.terminalProtocol is None:
            raise ConnectionDone
        self.terminalProtocol.clearInputLine()
        self.terminalProtocol.recv_char = True
        # because it's like a modal dialog box that steals focus, wait for at least 1 second
        # since the last keypress to avoid accidental input
        self.terminalProtocol.barrier(barrier)
        try:
            while True:
                type, value = self._receive()
                if type == 'key':
                    key = value[0]
                    if allowed is None or key in allowed:
                        self.terminalProtocol.lineBuffer.append(str(key))
                        self.terminalProtocol.terminal.write(str(key))
                        return type, value
                else:
                    return type, value
        finally:
            if self.terminalProtocol is not None:
                self.terminalProtocol.recv_char = False

    def ask_question(self, question, allowed, help=None, help_keys='hH?', barrier=1):
        with self.channel.locked_output():
            with self.new_prompt(question):
                if help is not None and '?' not in allowed:
                    allowed += help_keys
                while True:
                    try:
                        type, value = self.recv_char(allowed, barrier=barrier)
                        if type=='key':
                            value = value[0]
                            if help is not None and value in help_keys:
                                self.write(help)
                            else:
                                return value
                        else:
                            break
                    except ConnectionDone:
                        raise api.GreenletExit

    def write(self, msg):
        self.writecount += 1
        if self.terminalProtocol:
            self.terminalProtocol.addOutput(msg, async=True)
        else:
            if not msg.endswith('\n'):
                msg += '\n'
            msg = msg.replace('\n', '\r\n')
            __original_sys_stderr__.write(msg)

    def tell(self):
        "not a real tell, but useful for some purposes (trafficlog module)"
        return self.writecount

    def set_ps(self, ps, draw=None):
        if self.terminalProtocol:
            if draw is None:
                draw = self.terminalProtocol.receiving>0
            self.terminalProtocol.set_ps(ps, draw)

    def __iter__(self):
        return self

    def next(self):
        try:
            return self.recv()
        except ConnectionDone:
            raise StopIteration

    def copy_input_line(self, line=None):
        self.terminalProtocol.ps[0] = self.terminalProtocol.ps_init[0]
        self.terminalProtocol.cursorToBOL()
        self.terminal.eraseLine()
        self.terminalProtocol.drawInputLine(line)
        self.terminal.nextLine()

    def clear_input_line(self):
        self.terminalProtocol.clearInputLine()

    def disable(self):
        self.terminalProtocol.receiving -= 1

    def enable(self):
        self.terminalProtocol.receiving += 1
        self.terminalProtocol.cursorToBOL()
        self.terminal.eraseLine()
        self.terminalProtocol.ps[0] = self.terminalProtocol.ps_init[0]
        self.terminalProtocol.drawInputLine()


def get_console():
    buffer = GreenConsole()
    buffer.channel = ConsoleProtocol.channel
    p = ServerProtocol(ConsoleProtocol)
    stdio.StandardIO(p)
    buffer.protocol = p
    return buffer

__original_sys_stderr__ = sys.stderr
__original_sys_stdout__ = sys.stdout

class _WriteProxy(object):

    def __init__(self, original, console):
        self.original = original
        self.console = console
        self.state = None

    def __getattr__(self, item):
        return getattr(self.original, item)

    def write(self, data):
        if data=='\n' and self.state=='after write':
            self.state = 'skipped'
            return
        else:
            self.state = 'after write'
            return self.console.write(data)

def hook_std_output(console):
    sys.stderr = _WriteProxy(__original_sys_stderr__, console)
    sys.stdout = _WriteProxy(__original_sys_stdout__, console)

def restore_std_output():
    sys.stdout = __original_sys_stdout__
    sys.stderr = __original_sys_stderr__

@contextmanager
def setup_console():
    fd = sys.__stdin__.fileno()
    oldSettings = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    try:
        console = get_console()
        hook_std_output(console)
        try:
            yield console
        finally:
            restore_std_output()
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, oldSettings)
        terminal_initialize(fd)

def _fix():
    s = [16640, 5, 191, 35387, 15, 15,
         ['\x03', '\x1c', '\x7f', '\x15', '\x04', '\x00', '\x01', '\x00', '\x11', '\x13', '\x1a',
          '\x00', '\x12', '\x0f', '\x17', '\x16', '\x00', '\x00', '\x00', '\x00', '\x00', '\x00',
          '\x00', '\x00', '\x00', '\x00', '\x00', '\x00', '\x00', '\x00', '\x00', '\x00']]
    termios.tcsetattr(sys.__stdin__.fileno(), termios.TCSANOW, s)
    terminal_initialize(sys.__stdin__.fileno())

def main():
    if sys.argv[1:] == ['fix']:
        return _fix()
    from twisted.internet import reactor
    from application import log
    from datetime import datetime
    import traceback
    from msrplib.trafficlog import HeaderLogger_File

    def traffic():
        t = HeaderLogger_File(console)
        t.write_data_with_header('data1', '10.1.1.1:222 -> 10.2.2.2:111')
        t.write_data_with_header('data2', '10.1.1.1:222 -> 10.2.2.2:111')
        api.sleep(2)
        t.write_data_with_header('data3', '10.1.1.1:222 -> 10.2.2.2:111')
        t.write_data_with_header('data4', '10.1.1.1:222 -> 10.2.2.2:111')

    def incoming():
        import random
        from_ = random.randint(1, 10**10)
        q = 'Accept incoming session from %s@example.com? y/n/h ' % from_
        response = console.ask_question(q, list('ynYN\r\n')+[CTRL_D], help='y - yes\nn - no')
        if response is not None:
            print 'You said %r' % response

    def disable(seconds=5):
        seconds = int(seconds)
        print 'Disabling console for %s seconds. Ctrl-D should still work' % seconds
        def enable():
            print 're-enabling console'
            console.enable()
            print 'console re-enabled.'
        reactor.callLater(seconds, enable)
        console.disable()

    def sleep(seconds=5):
        print 'sleeping for %s seconds... Ctrl-C and Ctrl-\ should work' % seconds
        api.sleep(seconds)

    def sleeploop():
        while True:
            try:
                print '%s sync sleeping for 5 seconds... Ctrl-C and Ctrl-\ should work' % datetime.now()
                api.sleep(5)
            except KeyboardInterrupt:
                print '-------------- KeyboardInterrupt'
            except:
                print '-------------- catched an exception'
                traceback.print_exc()

    def exit():
        def func1():
            print "Sending exception to console's channel"
            console.channel.send_exception(ConnectionDone())
        api.spawn(func1)

    def help():
        print 'Type a command to execute or an expression to evaluate.'
        print 'Prepend your input with number of seconds to execute the command asynchronously'
        print 'Example commands:'
        print '1 1/0        # async exception'
        print 'sleep        # sync sleep'
        print '3 sleep      # async sleep'
        print 'incoming     # ask a question'
        print '3 incoming   # ask a question async after 3 seconds'
        print 'set_ps @>    # set prompt to @>'
        print 'disable      # disable the console for 5 seconds'
        print 'sys.exit()   # exit synchronously'

    def write(*args):
        return sys.stdout.write(*args)

    try:
        with setup_console() as console:
            console.terminalProtocol.send_keys.append('\x13') # ctrl-s
            console.set_ps('>>> ')
            for type, value in console:
                if type == 'line':
                    print '%s> %s' % (datetime.now().strftime('%X'), value)
                if type == 'key':
                    print 'handled %s %s' % (type, value)
                if type=='line' and value.strip():
                    args = value.split(' ')
                    seconds = None
                    if len(args)>1:
                        try:
                            seconds = float(args[0])
                            del args[0]
                        except (ValueError, KeyError):
                            pass
                    command, params = args[0], args[1:]
                    def run_command(command, params, globals, locals):
                        evaled_command = None
                        try:
                            evaled_command = eval(command, globals, locals)
                        except NameError, ex:
                            if hasattr(console, command):
                                evaled_command = getattr(console, command)
                            else:
                                print ex
                        if evaled_command is not None:
                            if callable(evaled_command):
                                evaled_command(*params)
                            else:
                                print evaled_command
                    if seconds is None:
                        run_command(command, params, globals(), locals())
                    else:
                        reactor.callLater(seconds, api.spawn, run_command, command, params, globals(), locals())
            if console.terminalProtocol.lineBuffer:
                console.copy_input_line()
            console.write('clean exit')
    except EOF:
        print 'EOF'
    except:
        print 'exception exit'
        raise

if __name__ == '__main__':
    main()
