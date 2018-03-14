import os
import os.path
import subprocess
import sys

import click

import pros.conductor
from .click_classes import PROSGroup


@click.group(cls=PROSGroup)
def build_cli():
    pass


@build_cli.command(aliases=['build'])
@click.argument('build-args', nargs=-1)
@click.pass_context
def make(ctx, build_args):
    """
    Build current PROS project or cwd
    """
    env = os.environ.copy()
    # Add PROS toolchain to the beginning of PATH to ensure PROS binaries are preferred
    if os.environ.get('PROS_TOOLCHAIN'):
        env['PATH'] = os.path.join(os.environ.get('PROS_TOOLCHAIN'), 'bin') + os.pathsep + env['PATH']

    # call make.exe if on Windows
    if os.name == 'nt' and os.environ.get('PROS_TOOLCHAIN'):
        make_cmd = os.path.join(os.environ.get('PROS_TOOLCHAIN'), 'bin', 'make.exe')
    else:
        make_cmd = 'make'
    cwd = os.getcwd()
    if pros.conductor.Project.find_project(os.getcwd()):
        cwd = os.path.dirname(pros.conductor.Project.find_project(os.getcwd()))
    process = subprocess.Popen(executable=make_cmd, args=[make_cmd, *build_args], cwd=cwd, env=env,
                               stdout=sys.stdout, stderr=sys.stderr)
    process.wait()
    if process.returncode != 0:
        ctx.exit(process.returncode)


@build_cli.command('make-upload', aliases=['mu'], hidden=True)
@click.pass_context
def make_upload(ctx):
    from .upload import upload
    ctx.forward(make)
    ctx.forward(upload)


@build_cli.command('make-upload-terminal', aliases=['mut'], hidden=True)
@click.pass_context
def make_upload_terminal(ctx):
    from .upload import upload
    from .terminal import terminal
    ctx.forward(make)
    ctx.forward(upload)
    ctx.forward(terminal, request_banner=False)


@build_cli.command('build-compile-commands', hidden=True)
def build_compile_commands():
    """
    Build a compile_commands.json compatible with cquery
    :return:
    """
    from libscanbuild.compilation import Compilation
    from libscanbuild.arguments import create_intercept_parser
    from tempfile import TemporaryDirectory

    def libscanbuild_capture(args):
        import argparse
        from libscanbuild.intercept import setup_environment, run_build, exec_trace_files, parse_exec_trace, \
            compilations
        from libear import temporary_directory
        # type: argparse.Namespace -> Tuple[int, Iterable[Compilation]]
        """ Implementation of compilation database generation.
        :param args:    the parsed and validated command line arguments
        :return:        the exit status of build process. """

        with temporary_directory(prefix='intercept-') as tmp_dir:
            # run the build command
            environment = setup_environment(args, tmp_dir)
            if os.environ.get('PROS_TOOLCHAIN'):
                environment['PATH'] = os.path.join(os.environ.get('PROS_TOOLCHAIN'), 'bin') + os.pathsep + environment[
                    'PATH']
            exit_code = run_build(args.build, env=environment)
            # read the intercepted exec calls
            calls = (parse_exec_trace(file) for file in exec_trace_files(tmp_dir))
            current = compilations(calls, args.cc, args.cxx)

            return exit_code, iter(set(current))

    # call make.exe if on Windows
    if os.name == 'nt' and os.environ.get('PROS_TOOLCHAIN'):
        make_cmd = os.path.join(os.environ.get('PROS_TOOLCHAIN'), 'bin', 'make.exe')
    else:
        make_cmd = 'make'
    with TemporaryDirectory() as td:
        bindir = td.replace("\\", "/") if os.sep == '\\' else td
        args = create_intercept_parser().parse_args(
            ['--override-compiler', '--use-cc', 'true', '--use-c++', 'true', make_cmd, 'all-obj',
             f'BINDIR={bindir}', 'CC=intercept-cc', 'CXX=intercept-c++', 'LD=true'])
        print(args)
        exit_code, entries = libscanbuild_capture(args)

    if exit_code:
        return exit_code

    import subprocess
    env = os.environ.copy()
    # Add PROS toolchain to the beginning of PATH to ensure PROS binaries are preferred
    if os.environ.get('PROS_TOOLCHAIN'):
        env['PATH'] = os.path.join(os.environ.get('PROS_TOOLCHAIN'), 'bin') + os.pathsep + env['PATH']
    cc_sysroot = subprocess.run([make_cmd, 'cc-sysroot'], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = str(cc_sysroot.stdout.decode()).splitlines()
    lines = [l.strip() for l in lines]
    cc_sysroot_includes = []
    copy = False
    for line in lines:
        if line == '#include <...> search starts here:':
            copy = True
            continue
        if line == 'End of search list.':
            copy = False
            continue
        if copy:
            cc_sysroot_includes.append(f'-I{line}')
    print(cc_sysroot_includes)
    cxx_sysroot = subprocess.run([make_cmd, 'cxx-sysroot'], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = str(cxx_sysroot.stdout.decode()).splitlines()
    lines = [l.strip() for l in lines]
    cxx_sysroot_includes = []
    copy = False
    for line in lines:
        if line == '#include <...> search starts here:':
            copy = True
            continue
        if line == 'End of search list.':
            copy = False
            continue
        if copy:
            cxx_sysroot_includes.append(f'-I{line}')
    print(cxx_sysroot_includes)
    with open(args.cdb, 'w') as handle:
        import json
        json_entries = []
        for entry in entries:
            if entry.compiler == 'c':
                entry.flags = cc_sysroot_includes + entry.flags
            else:
                entry.flags = cxx_sysroot_includes + entry.flags
            entry = entry.as_db_entry()
            entry['arguments'][0] = 'clang' if entry['arguments'][0] == 'cc' else 'clang++'
            json_entries.append(entry)
        json.dump(json_entries, handle, sort_keys=True, indent=4)
