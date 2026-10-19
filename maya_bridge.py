r"""Maya bridge client - send Python to a running Maya via commandPort.

Maya side (run once per Maya session in Script Editor > Python tab):
    import sys
    sys.path.append(r"C:\Users\alfre\Documents\Default Project")
    import maya_setup
    maya_setup.open_port(54321)

Then from here / from agent:
    from maya_bridge import run, run_file, ping
    ping()
    run("import maya.cmds as cmds; cmds.polySphere(n='test_ball')")
    run_file("chandelier_generator.py", "import chandelier_generator as cg; cg.build_chandelier()")
"""
import socket

HOST = "localhost"
PORT = 54321
TIMEOUT = 10


def _send_raw(code: str, timeout: int = TIMEOUT) -> str:
    """Send raw python string to Maya's commandPort, return Maya's reply."""
    # Maya's python commandPort expects a trailing newline/null handling;
    # \x00 terminates cleanly for some setups, plain \\n works for stp="python".
    if not code.endswith("\n"):
        code += "\n"
    with socket.create_connection((HOST, PORT), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(code.encode("utf-8"))
        # Maya closes connection after each command; read until close.
        chunks = []
        try:
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
        except socket.timeout:
            pass
        return b"".join(chunks).decode("utf-8", errors="replace")


def run(code: str, timeout: int = TIMEOUT) -> str:
    """Execute a code string inside Maya. Returns Maya output / errors."""
    return _send_raw(code, timeout)


def run_file(path: str, call: str = "", timeout: int = TIMEOUT) -> str:
    """Execute a .py file inside Maya via exec(open().read()) + optional call.

    Example: run_file("chandelier_generator.py", "cg.build_chandelier()")
    File path is resolved relative to this bridge file's directory.

    NOTE: the script is exec'd with ONE shared namespace for globals and
    locals. Maya's commandPort hands us code that already runs inside a
    try/except, so a plain exec() would put module-level imports into a
    separate locals dict -- functions defined in the file would then fail
    with "NameError: name 'x' is not defined". Sharing one dict keeps the
    file behaving like a normal Python script.
    """
    import pathlib
    p = pathlib.Path(path)
    if not p.is_absolute():
        p = pathlib.Path(__file__).parent / p
    p = p.resolve()
    # Use forward slashes - safe inside Maya python strings on Windows.
    fwd = p.as_posix()
    code = (
        "_ns = {'__name__': '__main__', '__file__': " + repr(fwd) + "}\n"
        "exec(compile(open(" + repr(fwd) + ").read(), " + repr(fwd) +
        ", 'exec'), _ns, _ns)\n"
    )
    if call:
        code += "exec(" + repr(call) + ", _ns)\n"
    # Wrap so tracebacks come back over the socket.
    wrapped = (
        "import traceback\n"
        "try:\n"
        + "".join("    " + line + "\n" for line in code.splitlines())
        + "except Exception:\n"
        "    traceback.print_exc()\n"
    )
    return _send_raw(wrapped, timeout)


def ping(timeout: int = 5) -> bool:
    """True if Maya commandPort answers."""
    try:
        out = _send_raw("print('maya_bridge_pong')\n", timeout)
        return "maya_bridge_pong" in out or out.strip() == ""
        # NOTE: stp="python" port often returns empty on print (goes to
        # Maya Script Editor history instead). Empty + no exception = alive.
    except (ConnectionRefusedError, TimeoutError, OSError, socket.timeout):
        return False
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "ping":
        print("ALIVE" if ping() else "NO_MAYA (open Maya + run maya_setup.open_port())")
    elif len(sys.argv) > 2 and sys.argv[1] == "run":
        print(run(" ".join(sys.argv[2:])))
    elif len(sys.argv) > 2 and sys.argv[1] in ("run_file", "file"):
        from pathlib import Path
        p = Path(sys.argv[2])
        if not p.is_absolute():
            p = Path(__file__).parent / p
        print(run_file(str(p)))
    else:
        print("usage: python maya_bridge.py ping")
        print("       python maya_bridge.py run <python code>")
        print("       python maya_bridge.py run_file <path.py>")
        print("ALIVE" if ping() else "NO_MAYA (open Maya + run maya_setup.open_port())")
