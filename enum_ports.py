import os

if os.name == 'nt':
    from serial.tools.list_ports_windows import comports
elif os.name == 'posix':
    from serial.tools.list_ports_posix import comports
else:
    raise ImportError("Sorry: no implementation for your platform ('{}') available".format(os.name))


def enum_ports():
    iterator = sorted(comports())
    for i in iterator:
        print(i)
        yield i
