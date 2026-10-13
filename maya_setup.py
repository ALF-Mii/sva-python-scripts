r"""Run ONCE inside Maya (Script Editor > Python tab) to open the bridge port.

    import sys
    sys.path.append(r"C:\Users\alfre\Documents\Default Project")
    import maya_setup
    maya_setup.open_port(54321)

Then leave Maya open. The agent sends code via maya_bridge.py from outside.
"""
PORT = 54321


def open_port(port=PORT):
    import maya.cmds as cmds
    name = "localhost:{}".format(port)
    # Already open?
    try:
        existing = cmds.commandPort(name, query=True)
        if existing:
            print("Bridge already open on {}".format(name))
            return name
    except Exception:
        pass
    # Maya 2022+: stp="python" (older: sourceType="python")
    try:
        cmds.commandPort(name=name, sourceType="python", echoOutput=True)
    except TypeError:
        cmds.commandPort(n=name, stp="python", eo=True)
    print("Maya bridge open on {} (sourceType=python). Leave Maya open.".format(name))
    return name


def close_port(port=PORT):
    import maya.cmds as cmds
    name = "localhost:{}".format(port)
    try:
        cmds.commandPort(name=name, close=True)
        print("Bridge closed on {}".format(name))
    except Exception as e:
        print("Close failed: {}".format(e))


if __name__ == "__main__":
    open_port()
