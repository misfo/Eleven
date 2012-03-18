import os, sys, inspect, socket, uuid
cmd_folder = os.path.abspath(os.path.split(inspect.getfile( inspect.currentframe() ))[0])
sys.path.append(os.path.join(cmd_folder, 'bencode'))
from bencode import bdecode, bencode, BTFailure, decode_func

def wait_for_ack(sock):
    sock.listen(1)
    conn, _ = sock.accept()
    data = ""
    while 1:
        chunk = conn.recv(8192)
        if not chunk: break
        data += chunk
    msg = bdecode(data)
    if msg['op'] == 'ack':
        return msg
    else:
        raise UserWarning("Unexpected op: " + msg['op'])

def bdecode_next(x):
    """
    Return the next bencoded value in the string and the rest of the undecoded string

    Assume that any errors while parsing are caused by not having the entire string
    """
    try:
        r, l = decode_func[x[0]](x, 0)
    except (IndexError, KeyError, ValueError):
        return (None, x)
    return (r, x[l:])

def _combine_responses(accum, resp):
    for k, v in resp.iteritems():
        if k in ('id', 'ns', 'session'):
            accum[k] = v
        elif k == 'value':
            if not accum.has_key(k): accum[k] = []
            accum[k].append(v)
        elif k == 'status':
            if not accum.has_key(k): accum[k] = set()
            accum[k].update(v)
        elif isinstance(v, basestring):
            if not accum.has_key(k): accum[k] = ""
            accum[k] += v
    return accum

def combine_responses(responses):
    """
    Combines the provided seq of response messages into a single response map.

    Certain message slots are combined in special ways:

      - only the last :ns is retained
      - :value is accumulated into an ordered collection
      - :status is accumulated into a set
      - string values (associated with e.g. :out and :err) are concatenated

    ported from clojure.tools.nrepl/combine-responses
    """
    return reduce(_combine_responses, responses, {})

class NreplClient(object):

    def __init__(self, host, port):
        self.sock = socket.socket()
        self.sock.connect((host, port))

    def eval(self, code, callbacks={}):
        self.send_msg({'op': 'eval', 'code': str(code)}, callbacks)

    def kill_server(self, callbacks={}):
        self.eval('(System/exit 0)', callbacks)

    def send_msg(self, msg, callbacks):
        if not msg.has_key('id'):
            msg = dict(msg, id=str(uuid.uuid4()))

        outgoing_data = bencode(msg)
        bytes_to_send = len(outgoing_data)
        bytes_sent = 0
        while bytes_sent < bytes_to_send:
            sent = self.sock.send(outgoing_data)
            bytes_sent += sent
            outgoing_data = outgoing_data[sent:]

        get_callback = dict.get if isinstance(callbacks, dict) else getattr
        on_sent   = get_callback(callbacks, 'on_sent', None)
        on_msg    = get_callback(callbacks, 'on_msg', None)
        on_out    = get_callback(callbacks, 'on_out', None)
        on_err    = get_callback(callbacks, 'on_err', None)
        on_value  = get_callback(callbacks, 'on_value', None)
        on_status = get_callback(callbacks, 'on_status', None)
        on_done   = get_callback(callbacks, 'on_done', None)
        print ">>> on_done\n", repr(on_done)

        if on_sent: on_sent(msg)

        in_buffer = ""
        responses = []
        while 1:
            if not in_buffer:
                in_buffer += self.sock.recv(8192)

            msg, in_buffer = bdecode_next(in_buffer)
            if msg == None:
                in_buffer += self.sock.recv(8192)
                continue
            print ">>> msg\n", repr(msg)

            if on_done: responses.append(msg)

            if on_msg: on_msg(msg)

            if   on_out   and msg.has_key('out'):   on_out(msg)
            elif on_err   and msg.has_key('err'):   on_err(msg)
            elif on_value and msg.has_key('value'): on_value(msg)

            status = msg.get('status')
            if status:
                if on_status: on_status(msg)
                if "done" in status:
                    if on_done:
                        response = combine_responses(responses)
                        on_done(response)
                    #TODO don't return if 'daemon' option
                    return
