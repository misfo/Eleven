import re, os, socket, string, subprocess, thread, threading, time
import sublime, sublime_plugin
from functools import partial
from string import Template

max_cols = 60
repls_file = ".eleven.json"

def clean(str):
    return str.translate(None, '\r') if str else None

def template_string_keys(template_str):
    return [m[1] or m[2] for m
            in Template.pattern.findall(template_str)
            if m[1] or m[2]]

def call_with_args_merged(func, args, more_args):
    """Call func with more_args merged into args (useful w/ partial)"""
    args = args.copy()
    args.update(more_args)
    return func(args)

def selection(view):
    sel = view.sel()
    if len(sel) == 1 and not sel[0].empty():
        return view.substr(sel[0]).strip()
    else:
        raise UserWarning("There must be one selection to evaluate")

def symbol_under_cursor(view):
    begin = end = view.sel()[0].begin()
    while symbol_char(view.substr(begin - 1)): begin -= 1
    while symbol_char(view.substr(end)): end += 1
    if begin == end:
        raise UserWarning("No symbol found under cursor")
    else:
        return view.substr(sublime.Region(begin, end))

def from_input_panel(window, options, on_done):
    initial_text_chunks = options['initial_text']
    initial_text = "".join(initial_text_chunks) if initial_text_chunks else ""
    input_view = window.show_input_panel(options['prompt'],
                                         initial_text,
                                         on_done, None, None)

    if initial_text_chunks and len(initial_text_chunks) > 1:
        input_view.sel().clear()
        offset = 0
        for chunk in initial_text_chunks[0:-1]:
            offset += len(chunk)
            input_view.sel().add(sublime.Region(offset))

def symbol_char(char):
    return re.match("[-\w*+!?/.<>]", char)

def project_path(dir_name):
    if os.path.isfile(os.path.join(dir_name, 'project.clj')):
        return dir_name
    elif dir_name == "/":
        return None
    else:
        return project_path(os.path.dirname(dir_name))

def classpath_relative_path(file_name):
    (abs_path, ext) = os.path.splitext(file_name)
    segments = []
    while 1:
        (abs_path, segment) = os.path.split(abs_path)
        if segment == "src": return string.join(segments, "/")
        segments.insert(0, segment)

def output_to_view(v, text):
    start = v.size()
    v.set_read_only(False)
    edit = v.begin_edit()
    end = start + v.insert(edit, start, text)
    v.end_edit(edit)
    v.set_read_only(True)
    return sublime.Region(start, end)


def get_repl_servers():
    return sublime.load_settings(repls_file).get('repl_servers') or {}

def set_repl_servers(repl_servers, save=True):
    sublime.load_settings(repls_file).set('repl_servers', repl_servers)
    if save:
        sublime.save_settings(repls_file)


def start_repl_server(window_id, cwd):
    proc = subprocess.Popen(["lein", "repl"], stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE,
                                              cwd=cwd)
    stdout, stderr = proc.communicate()
    match = re.search(r"listening on localhost port (\d+)", stdout)
    if match:
        port = int(match.group(1))
        sublime.set_timeout(partial(on_repl_server_started, window_id, port), 0)
    else:
        sublime.error_message("Unable to start a REPL with `lein repl`")

def on_repl_server_started(window_id, port):
    repl_servers = get_repl_servers()
    repl_servers[str(window_id)] = port
    set_repl_servers(repl_servers)
    sublime.status_message("Clojure REPL started on port " + str(port))

def on_repl_died(window_id):
    sublime.error_message("The REPL server for this window died. "
                          + "Please try again.")
    repl_servers = get_repl_servers()
    del repl_servers[str(window_id)]
    set_repl_servers(repl_servers)


def get_repl_view(window):
    try:
        return (v for v
                in window.views()
                if v.settings().get('clojure_repl')).next()
    except StopIteration:
        return None

# persistent REPL clients for each window that keep their dynamic vars
clients = {}

class ReplClient:
    def __init__(self, port):
        self.ns = None

        self.sock = socket.socket()
        self.sock.connect(('localhost', port))
        self.sock.settimeout(10)

    def evaluate(self, exprs, on_complete=None):
        try:
            if not self.ns:
                _, self.ns = self._recv_until_prompted()

            results = []
            for expr in exprs:
                self.sock.send(expr + "\n")
                output, next_ns = self._recv_until_prompted()
                results.append({'ns': self.ns, 'expr': expr, 'output': output})
                self.ns = next_ns
        except (socket.error, EOFError) as e:
            if type(e) != EOFError and e.errno != 54:
                raise e
            results = None

        return_val = {'results': results, 'resulting_ns': self.ns}
        if on_complete:
            sublime.set_timeout(partial(on_complete, return_val), 0)
        else:
            return return_val

    def kill(self):
        try:
            self.evaluate(["(System/exit 0)"])
        except socket.error:
            # Probably already dead
            pass

    def _recv_until_prompted(self):
        output = ""
        while 1:
            output += self.sock.recv(1024)
            match = re.match(r"(.*\n)?(\S+)=> $", output, re.DOTALL)
            if match:
                return (clean(match.group(1)), match.group(2))
            elif output == "":
                raise EOFError


class ClojureStartRepl(sublime_plugin.WindowCommand):
    def run(self):
        wid = self.window.id()
        repl_servers = get_repl_servers()
        port = repl_servers.get(str(wid))
        if port:
            client = clients.get(wid)
            if client:
                return
            try:
                clients[wid] = ReplClient(port)
                return
            except socket.error:
                del repl_servers[str(wid)]
                set_repl_servers(repl_servers)

        sublime.status_message("Starting Clojure REPL")

        thread.start_new_thread(start_repl_server, (wid, self._project_path()))

    def _project_path(self):
        folders = self.window.folders()
        for folder in folders:
            path = project_path(folder)
            if path: return path

        file_name = self.window.active_view().file_name()
        if file_name:
            dir_name = os.path.dirname(file_name)
            return project_path(dir_name) or dir_name

        return None


class ClojureEval(sublime_plugin.WindowCommand):
    def run(self, exprs = None, input_panel = None, **kwargs):
        self.window.run_command('clojure_start_repl')

        on_done = partial(self._handle_input, exprs, **kwargs)
        need_input_panel = (expr for expr
                            in exprs
                            if 'from_input_panel' in template_string_keys(expr))
        try:
            need_input_panel.next()
            from_input_panel(self.window, input_panel, on_done)
        except StopIteration:
            on_done(None)

    def _handle_input(self, exprs,
                            from_input_panel,
                            handler_command="clojure_output_to_repl",
                            handler_args={},
                            **kwargs):
        wid = self.window.id()
        port = get_repl_servers().get(str(wid))
        if not port:
            sublime.set_timeout(partial(self._handle_input,
                                        exprs,
                                        from_input_panel,
                                        handler_command,
                                        handler_args
                                        **kwargs), 100)
            return

        exprs = (Template(expr).safe_substitute(from_input_panel=from_input_panel)
                 for expr in exprs)

        try:
            if handler_command == "clojure_output_to_repl":
                client = clients.get(wid)
                if not client or not get_repl_view(self.window):
                    client = ReplClient(port)
                    clients[wid] = client

            else:
                client = ReplClient(port)
        except socket.error as e:
            if e.errno != 61:
                raise e
            client = None

        run_handler = partial(self.window.run_command, handler_command)
        on_complete = partial(call_with_args_merged, run_handler, handler_args)
        if client:
            thread.start_new_thread(client.evaluate, (exprs, on_complete))
        else:
            print "client socket failed to open"
            on_complete(None)

class ClojureEvalFromView(sublime_plugin.TextCommand):
    def run_(self, args):
        if 'event' in args:
            del args['event']
        return self.run(**args)

    def run(self, exprs = None, **kwargs):
        input_keys = [template_string_keys(expr) for expr in exprs]
        input_keys = [item for sublist in input_keys for item in sublist]
        input_mapping = {}

        try:
            for key in ['selection', 'symbol_under_cursor']:
                if key in input_keys:
                    input_mapping[key] = globals()[key](self.view)
        except UserWarning as warning:
            sublime.status_message(str(warning))
            return

        exprs = [Template(expr).safe_substitute(input_mapping) for expr in exprs]

        file_name = self.view.file_name()
        if file_name:
            path = classpath_relative_path(file_name)
            file_ns = re.sub("/", ".", path)
            exprs.insert(0, "(do (load \"/" + path + "\") "
                            + "(in-ns '" + file_ns + "))")

        kwargs.update(exprs = exprs)
        self.view.window().run_command('clojure_eval', kwargs)


class ClojureOutputToRepl(sublime_plugin.WindowCommand):
    def run(self, results = None, resulting_ns = None):
        if results == None:
            on_repl_died(self.window.id())
            return

        view = self._find_or_create_repl_view()
        bookmark_point = sublime.Region(view.size())

        output = Template("$ns=> $expr\n$output\n\n").safe_substitute(results[-1])
        output_region = output_to_view(view, output)

        view.sel().clear()
        view.sel().add(bookmark_point)
        bookmarks = view.get_regions('bookmarks')
        bookmarks.append(bookmark_point)
        view.add_regions('bookmarks', bookmarks, 'bookmarks', 'bookmark',
                         sublime.HIDDEN | sublime.PERSISTENT)

        view.set_name("(in-ns '" + resulting_ns + ")")

        active_view = self.window.active_view()
        active_group = self.window.active_group()
        repl_view_group, _ = self.window.get_view_index(view)
        self.window.focus_view(view)
        if repl_view_group != active_group:
            # give focus back to the originally active view if it's in a
            # different group
            self.window.focus_view(active_view)

        # not sure why this has to be reversed
        view.show(sublime.Region(output_region.b, output_region.a))

    def _find_or_create_repl_view(self):
        view = get_repl_view(self.window)

        if not view:
            view = self.window.new_file()
            view.set_scratch(True)
            view.set_read_only(True)
            view.settings().set('clojure_repl', True)
            view.settings().set('line_numbers', False)
            view.set_syntax_file('Packages/Eleven/Clojure REPL.tmLanguage')

        return view


class ClojureOutputToPanel(sublime_plugin.WindowCommand):
    def run(self, results = None, resulting_ns = None):
        if results == None:
            on_repl_died(self.window.id())
            return

        view = self.window.get_output_panel('clojure_output')
        output_region = output_to_view(view, results[-1]['output'])
        self.window.run_command("show_panel",
                                {"panel": "output.clojure_output"})

class ClojureOutputToView(sublime_plugin.WindowCommand):
    def run(self, results = None,
                  resulting_ns = None,
                  syntax_file = 'Packages/Clojure/Clojure.tmLanguage',
                  view_name = '$expr'):
        if results == None:
            on_repl_died(self.window.id())
            return

        view = self.window.new_file()
        view.set_scratch(True)
        view.set_read_only(True)
        if syntax_file:
            view.set_syntax_file(syntax_file)

        output_region = output_to_view(view, results[-1]['output'])

        view.sel().clear()
        view.sel().add(sublime.Region(0))
        view.show(0)


class ReplServerKiller(sublime_plugin.EventListener):
    def on_close(self, view):
        wids = [str(w.id()) for w in sublime.windows()]
        servers = get_repl_servers()
        active_servers = dict((w, servers[w]) for w in wids if w in servers)
        kill_ports = set(servers.values()) - set(active_servers.values())

        # Bring out yer dead!
        for port in kill_ports:
            repl = ReplClient(port)
            thread.start_new_thread(repl.kill, ())

        if servers != active_servers:
            set_repl_servers(active_servers)