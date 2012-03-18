# These commands exist only to reduce duplication in the *.sublime-keymapping
# files.  For instance, if ClojureMacroexpand wasn't defined then something
# like this would be in all the keymapping files:
#
#    {
#        "keys": ["super+m"],
#        "command": "clojure_eval_from_view",
#        "args": {"expr": "(macroexpand '${selection})"}
#    }

import sublime_plugin

class ClojureMacroexpand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.view.run_command('clojure_eval_from_view',
                              {'expr': "(macroexpand '${selection})"})

class ClojureViewDoc(sublime_plugin.TextCommand):
    def run(self, edit):
        self.view.run_command('clojure_eval_from_view',
            {"expr": "(clojure.repl/doc ${symbol_under_cursor})",
             "handler": "eleven_handlers.OutputToPanel"})

class ClojureViewSource(sublime_plugin.TextCommand):
    def run(self, edit):
        self.view.run_command('clojure_eval_from_view',
            {"expr": "(clojure.repl/source ${symbol_under_cursor})",
             "handler": "eleven_handlers.OutputToView"})
