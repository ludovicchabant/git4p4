import logging
import marshal
import re
import subprocess
import sys
import tempfile


logger = logging.getLogger(__name__)


class P4Error(Exception):
    pass


class P4EntryNotFound(Exception):
    pass


def encode_dict(d):
    out = {}
    for k, v in d.items():
        if isinstance(k, str):
            k = k.encode('utf8')
        if isinstance(v, str):
            v = v.encode('utf8')
        out[k] = v
    return out


def run_command(cmdargs, stdin=None, stdin_mode='w+b',
                only1=None, raise_error=True):
    cmdline = ['p4', '-G', '-zprog=git4p4', '-ztag'] + cmdargs

    stdin_file = None
    if stdin:
        stdin_file = tempfile.TemporaryFile(prefix="p4stdin", mode=stdin_mode)
        if isinstance(stdin, str):
            stdin_file.write(stdin.encode('utf8'))
        elif isinstance(stdin, bytes):
            stdin_file.write(stdin)
        elif isinstance(stdin, list):
            for line in stdin:
                stdin_file.write(line.encode('utf8'))
                stdin_file.write(b'\n')
        elif isinstance(stdin, dict):
            stdin = encode_dict(stdin)
            marshal.dump(stdin, stdin_file, 0)
        else:
            raise ValueError("stdin")
        stdin_file.flush()
        stdin_file.seek(0)

    logger.debug("P4: %s" % " ".join(cmdline))
    proc = subprocess.Popen(cmdline, stdin=stdin_file,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    outdata = []
    while True:
        try:
            rawentry = marshal.load(proc.stdout)
        except EOFError:
            break

        entry = {}
        for k, v in rawentry.items():
            if isinstance(k, bytes):
                k = k.decode('utf8')
            if isinstance(v, bytes):
                v = v.decode('utf8')
            entry[k] = v

        outdata.append(entry)

    outerr = proc.stderr.read()

    returncode = proc.wait()

    if returncode != 0 and raise_error:
        errmsg = outdata[0].get('data') if outdata else outerr
        raise P4Error(returncode, errmsg)
    outdata.append({"returncode": returncode, "ok": (returncode == 0)})

    if only1:
        return get_first_code_entry(only1, outdata)

    return outdata


def get_first_code_entry(entry_type, outdata, raise_not_found=True):
    entries = get_all_code_entries(entry_type, outdata,
                                   raise_not_found=raise_not_found)
    return entries[0]


def get_all_code_entries(entry_type, outdata, raise_not_found=True):
    entries = []
    for entry in outdata:
        if entry.get('code') == entry_type:
            entries.append(entry)
    if not entries and raise_not_found:
        raise P4EntryNotFound()
    return entries


def get_created_changelist_id(outdata, raise_error=True):
    m = re.match(r"Change (?P<id>\d+) created", outdata["data"])
    if m:
        return m.group("id")
    if raise_error:
        raise P4Error("Can't parse P4 output")
    return None

def get_change_spec_header():
    cmdline = ['p4', 'change', '-o']
    proc = subprocess.run(cmdline, encoding='utf8', stdout=subprocess.PIPE)

    header = []
    for line in proc.stdout.splitlines():
        if line.startswith('#'):
            header.append(line)
        else:
            break
    return '\n'.join(header)


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    outdata = run_command(sys.argv[1:])
    print(outdata)
