import re, os, socket, subprocess, thread
from functools import partial
from string import Template
import sublime, sublime_plugin
import nrepl

def resolve_attr(qualified_attr):
    namespace, attr_name = re.match(r"(.+)\.([^.]+)", qualified_attr).groups()
    return getattr(__import__(namespace), attr_name)

def template_string_keys(template_str):
    return [m[1] or m[2] for m
            in Template.pattern.findall(template_str)
            if m[1] or m[2]]

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

def insert_into_view(v, start, text):
    v.set_read_only(False)
    edit = v.begin_edit()
    end = start + v.insert(edit, start, text)
    v.end_edit(edit)
    v.set_read_only(True)
    return sublime.Region(start, end)

def append_to_view(v, text):
    return insert_into_view(v, v.size(), text)

def append_to_region(v, region_name, text):
    regions = v.get_regions(region_name)
    start = regions[-1].end()
    region = insert_into_view(v, start, text)
    regions.append(region)
    v.add_regions(region_name, regions, '')

def get_repl_view(window):
    try:
        return (v for v
                in window.views()
                if v.settings().get('nrepl_port')).next()
    except StopIteration:
        return None

class ClojureStartRepl(sublime_plugin.WindowCommand):
    def is_enabled(self):
        return not get_repl_view(self.window)

    def run(self):
        cwd = self._project_path()

        repl_view = self.window.new_file()

        repl_view.set_name("nREPL launching...")
        repl_view.set_scratch(True)
        repl_view.set_read_only(True)
        repl_view.settings().set('nrepl_port', None)
        repl_view.settings().set('nrepl_cwd', cwd)
        repl_view.settings().set('line_numbers', False)
        repl_view.set_syntax_file('Packages/Clojure/Clojure.tmLanguage')

        append_to_view(repl_view,
                       ("; running `lein repl :headless`%s...\n"
                        % ("in " + cwd if cwd else "")))
        thread.start_new_thread(self._start_server, (repl_view, cwd))

    def _project_path(self):
        folders = self.window.folders()
        for folder in folders:
            path = project_path(folder)
            if path: return path

        active_view = self.window.active_view()
        if active_view:
            file_name = active_view.file_name()
            if file_name:
                dir_name = os.path.dirname(file_name)
                return project_path(dir_name) or dir_name

        return None

    def _start_server(self, repl_view, cwd):
        ack_sock = socket.socket()
        ack_sock.bind(('localhost', 0))
        _, ack_port = ack_sock.getsockname()
        env = os.environ.copy()
        env.update(LEIN_REPL_ACK_PORT=str(ack_port))
        proc = subprocess.Popen(["lein2", "repl", ":headless"],
                                cwd=cwd, env=env)
        ack = nrepl.wait_for_ack(ack_sock)
        ack_sock.close()
        port = ack['port']

        sublime.set_timeout(partial(self._on_connected, repl_view, port), 0)

    def _on_connected(self, repl_view, port):
        repl_view.set_name("nREPL connected")
        repl_view.settings().set('nrepl_port', port)
        append_to_view(repl_view,
            (("; nREPL server started on port %d\n" +
              "; closing all REPL views using this port will kill the server\n")
             % port))
        repl_view.window().run_command('clojure_eval', {'expr': '(str "These ones go up to " (inc 10))'})

class NreplManager(sublime_plugin.EventListener):
    #TODO show nREPL status in status bar

    def on_close(self, view):
        port = view.settings().get('nrepl_port')
        if port:
            # If there are any other views using that server, don't kill it.
            for w in sublime.windows():
                for v in w.views():
                    if v.settings().get('nrepl_port') == port and v.id() != view.id():
                        return

            client = nrepl.NreplClient('localhost', port)
            thread.start_new_thread(client.kill_server, ())

    def on_load(self, view):
        port = view.settings().get('nrepl_port')
        if port:
            # We're loading a REPL window that existed from a hot exit.  Check
            # if nREPL is still running on that port.
            try:
                nrepl.NreplClient('localhost', port)
                print "nREPL on port %d is running" % port
            except:
                view.settings().set('nrepl_port', None)
                append_to_view(view, ("\n\n" +
                                      ";;;;;;;;;;;;;;;;\n" +
                                      "; DISCONNECTED ;\n" +
                                      ";;;;;;;;;;;;;;;;"))

class ClojureEval(sublime_plugin.WindowCommand):
    def is_enabled(self):
        return bool(get_repl_view(self.window))

    def run(self, expr = None, input_panel = None, **kwargs):
        on_done = partial(self._handle_input, expr, **kwargs)
        if 'from_input_panel' in template_string_keys(expr):
            from_input_panel(self.window, input_panel, on_done)
        else:
            on_done(None)

    def _handle_input(self, expr, from_input_panel,
                      handler="eleven_handlers.OutputToRepl", handler_args={}):
        repl_view = get_repl_view(self.window)
        expr = Template(expr).safe_substitute(from_input_panel=from_input_panel)

        port = int(repl_view.settings().get('nrepl_port'))
        client = nrepl.NreplClient('localhost', port)
        handler_class = resolve_attr(handler)
        handler_obj = handler_class(args=handler_args,
                                    window=self.window,
                                    repl_view=repl_view)
        thread.start_new_thread(client.eval, (expr, handler_obj))


class ClojureEvalFromView(sublime_plugin.TextCommand):
    def is_enabled(self):
        return bool(get_repl_view(self.view.window()))

    def run(self, edit, expr, **kwargs):
        input_keys = template_string_keys(expr)
        input_mapping = {}

        try:
            for key in ['selection', 'symbol_under_cursor']:
                if key in input_keys:
                    input_mapping[key] = globals()[key](self.view)
        except UserWarning as warning:
            sublime.status_message(str(warning))
            return

        expr = Template(expr).safe_substitute(input_mapping)

        # file_name = self.view.file_name()
        # if file_name:
        #     file_ns = re.sub("/", ".", path)
        #     expr = ("(do (load-file \"%s\") (in-ns '%s) %s)"
        #             % (file_name, file_ns, expr))

        kwargs.update(expr = expr)
        self.view.window().run_command('clojure_eval', kwargs)


class nrepl_handler(object):
    def __init__(self, args, window, repl_view):
        self.args = args
        self.window = window
        self.repl_view = repl_view
        for callback in ('on_sent', 'on_out', 'on_err', 'on_value',
                         'on_status', 'on_done'):
            unwrapped = "_%s" % callback
            if hasattr(self, unwrapped):
                setattr(self, callback, self._wrapped_callback(unwrapped))

    def on_sent(self, req):
        self.req = req

    def _wrapped_callback(self, method_name):
        method = getattr(self, method_name)
        return lambda m: sublime.set_timeout(partial(method, m), 0)
