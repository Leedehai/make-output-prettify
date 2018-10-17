#!/usr/bin/env python
# BSD license
#
# Make formatter engine: prettify the output of some make commands (less verbose) in
# real time. It works with multithreaded make as well.
# Call:  make-formatter.py make
#        make-formatter.py make -j8

# switch: if False, all output lines are passed through as is.
enable = True

import sys, signal
import subprocess
from os.path import normpath
from os.path import basename

cmd = sys.argv[1:]
if len(cmd) == 0:
    print("[Error] no command specified")
    sys.exit(1)
elif cmd[0] == "-h" or cmd[0] == "--help":
    print("Formatting script to make Make's output succinct while preserving error messages")
    print("Usage example: ./me make")
    print("               ./me make -j8")
    sys.exit(0)
elif cmd[0] != "make":
    print("[Error] it is not a make command")
    sys.exit(1)
elif "run" in cmd or "runraw" in cmd:
    print("[Error] this command should be run directly without me")
    sys.exit(1)

# key symbols
K_SHAREDLIB = ".so"
K_OBJ       = ".o"
K_CPP       = ".cc"
K_ASM       = ".s"
K_COMPILE   = " -c"
K_OUTPUT    = " -o"
K_FPIC      = " -fPIC"
K_PREPAR    = "Preparation: "
K_STARS     = "***"
K_MAKEDONE  = "make: DONE "

# prefixes
PREFIX_COMPILE   = "\x1b[38;5;220m[Compile]\x1b[0m" # yellow-ish
PREFIX_LINK      = "\x1b[38;5;45m[Link]\x1b[0m"   # red-ish
PREFIX_COMPLINK  = "\x1b[38;5;41m[Compile + Link]\x1b[0m" # green-ish
PREFIX_SHAREDLIB = "\x1b[38;5;225m[Library]\x1b[0m" # purple-ish

def sighandler(sig, frame):
    if sig == signal.SIGINT:
        print(" [SIGNAL] SIGINT sent to script")
    elif sig == signal.SIGTERM:
        print(" [SIGNAL] SIGTERM sent to script")
    else:
        print(" [SIGNAL] Signal %d sent to script" % sig)
    sys.exit(1)

# Set the signal handlers
signal.signal(signal.SIGINT, sighandler)
signal.signal(signal.SIGTERM, sighandler)

"""
handlers for different types of lines.
Interface: param: str: the line, guaranteed not "" and not starting with space
           return: tuple ([0] str: the processed line, [1] bool: should be printed)
"""

def handle_compile_only(line):
    line_split = line.split()
    if line.count(K_OUTPUT.strip()) != 0:
        obj_file_index = line_split.index(K_OUTPUT.strip()) + 1
        processed_line = "%s => %s" % (PREFIX_COMPILE, line_split[obj_file_index])
    else:
        source_files = [basename(item.replace(K_CPP, K_OBJ)) for item in line_split if item.endswith(K_CPP)]
        source_files += [basename(item.replace(K_ASM, K_OBJ)) for item in line_split if item.endswith(K_ASM)]
        processed_line = "%s => %s" % (PREFIX_COMPILE, ' '.join(source_files))
    return processed_line, True

def handle_convert_obj_to_so(line):
    line_split = line.split()
    so_file_index = line_split.index(K_OUTPUT.strip()) + 1
    processed_line = "%s => %s" % (PREFIX_SHAREDLIB, normpath(line_split[so_file_index]))
    return processed_line, True

def handle_compile_and_link(line):
    line_split = line.split()
    exe_file_index = line_split.index(K_OUTPUT.strip()) + 1
    if exe_file_index >= len(line_split): # unlikely
        return "%s => a.out" % (PREFIX_COMPLINK), True
    processed_line = "%s => %s" % (PREFIX_COMPLINK, normpath(line_split[exe_file_index]))
    return processed_line, True

def handle_link_only(line):
    line_split = line.split()
    exe_file_index = line_split.index(K_OUTPUT.strip()) + 1
    if exe_file_index >= len(line_split): # unlikely
        return "%s => a.out" % (PREFIX_LINK), True
    processed_line = "%s => %s" % (PREFIX_LINK, normpath(line_split[exe_file_index]))
    return processed_line, True

def handle_preparation_msg(line):
    return "", False

def handle_separator(line):
    return "", False

def handler_makedone_message(line):
    return "", False

def handle_passthrough(line):
    return line, True

"""
Check if the line is some tool's warning or error message. It should NOT rely on the
content of that tool's message pattern, as the pattern depends on the tool's authors.
Therefore, I need to use some heuristics.
@param str, guaranteed not "" but might starting with space
@return bool
"""
def is_error_msg(line):
    if line[0].isspace():
        return True # this line is a error (or warning) message from a tool
    elif line.startswith(K_STARS) or line.startswith(K_MAKEDONE) or line.count(K_PREPAR) != 0:
        return False

    first = line.split()[0]
    if first.endswith("g++") != 0 or line.endswith("gcc") != 0: # "clang++", "g++", "gcc"
        return False
    if first == "ld" or first == "ar" or first == "gold": # "ld", "ar", "gold"
        return False
    if first.count('-') == 1: # "g++-7"
        e1, e2 = first.split('-')[0], first.split('-')[1]
        if (e1.endswith("g++") or e1.endswith("gcc")) and e2.isdigit():
            return False
    return True # this line is a error (or warning) message from a tool

"""
Discern what the line is, and return a suitable function that handles it
NOTE the order of the condition checks are specifically arranged like this, modify with care
@param str
@return function
"""
def get_processing_handler(line):
    if len(line) == 0 or is_error_msg(line):
        return handle_passthrough
    # from here: is not empty, is not error message
    if line.count(K_COMPILE) != 0:
        return handle_compile_only
    # from here: no K_COMPILE found
    if line.count(K_FPIC) != 0:
        return handle_convert_obj_to_so
    # from here: no K_FPIC found
    if line.count(K_OUTPUT) != 0:
        if line.count(K_CPP) != 0 or line.count(K_ASM) != 0:
            return handle_compile_and_link
        else:
            return handle_link_only
    # from here: no K_OUTPUT found
    if line.count(K_PREPAR):
        return handle_preparation_msg
    # from here: no K_PREPAR found
    if line.count(K_STARS) != 0:
        return handle_separator
    # from here: no K_STARS found
    if line.count(K_MAKEDONE) != 0:
        return handler_makedone_message
    # from here: no K_MAKEDONE found
    return handle_passthrough

"""
@param str
@return tuple (str, bool)
"""
def process(line):
    if not enable:
        return (line, True)
    handler = get_processing_handler(line)
    return handler(line) # return (str, bool)

# main work
p = subprocess.Popen(' '.join(cmd), shell=True, stdout=subprocess.PIPE, bufsize=1)
for raw_line in iter(p.stdout.readline, b''):
    line, to_print = process(raw_line.decode('utf-8').rstrip())
    if to_print:
        print(line)
p.wait()
