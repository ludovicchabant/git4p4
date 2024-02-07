import subprocess
import sys
import logging


logger = logging.getLogger(__name__)


class GitError(Exception):
    pass


def run_command(cmdargs, raise_error=True, return_stderr=False, split_lines=False):
    cmdline = ['git'] + cmdargs

    logger.debug("Git: %s" % " ".join(cmdline))
    proc = subprocess.Popen(cmdline,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    outdata = proc.stdout.read().decode('utf8')
    outerr = proc.stderr.read().decode('utf8')

    returncode = proc.wait()

    if returncode != 0 and raise_error:
        errmsg = outerr
        raise GitError(returncode, errmsg)

    if split_lines:
        if outdata:
            outdata = outdata.splitlines()
        if outerr:
            outerr = outerr.splitlines()

    if return_stderr:
        return outdata, outerr
    return outdata


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    stdout, stderr = run_command(sys.argv[1:], return_stderr=True)
    print("stdout:\n")
    print(stdout)
    print()
    if stderr:
        print("stderr:\n")
        print(stderr)
        print()
