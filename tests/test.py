#!/usr/bin/env python
# PYTHON_ARGCOMPLETE_OK

import argparse
import os
import subprocess
import sys

try:
    import argcomplete
except ImportError:
    argcomplete = None

base_path = os.path.expanduser('~/shippable-migration')
migration_tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'migrate.py')

repos = {
    'ansible-collections/amazon.aws': [
        'main',
    ],
    'ansible-collections/community.general': [
        'main',
        'stable-1',
    ],
    'ansible-collections/community.windows': [
        'main',
    ],
    'ansible-collections/community.postgresql': [
        'main',
    ],
    'ansible-collections/community.network': [
        'main',
        'stable-1',
    ],
    'ansible-collections/community.docker': [
        'main',
    ],
    'ansible-collections/community.crypto': [
        'main',
    ],
    'ansible-collections/community.aws': [
        'main',
    ],
    'ansible-collections/ansible.posix': [
        'main',
    ],
    'ansible-collections/ansible.windows': [
        'main',
    ],
    'ansible-collections/hetzner.hcloud': [
        'master',
    ],
    'ansible-collections/community.azure': [
        'master',
    ],
    'ansible-collections/community.rabbitmq': [
        'main',
    ],
    'ansible-collections/community.libvirt': [
        'main',
    ],
    'ansible-collections/azure': [
        'dev',
        'master',
    ],
}


def update():
    for repo, branches in repos.items():
        for branch in branches:
            path = os.path.join(base_path, repo, branch)

            if os.path.exists(path):
                subprocess.run(['git', 'checkout', '.'], check=True, cwd=path)
                subprocess.run(['git', 'clean', '-fxd'], check=True, cwd=path)
                subprocess.run(['git', 'pull'], check=True, cwd=path)
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                subprocess.run(['git', 'clone', f'https://github.com/{repo}', '--branch', branch, path], check=True)


def migrate():
    for repo, branches in repos.items():
        for branch in branches:
            path = os.path.join(base_path, repo, branch)

            print(f'Processing {repo} branch {branch} ...')

            try:
                process = subprocess.run([migration_tool, path], check=True, capture_output=True)
            except subprocess.CalledProcessError as ex:
                sys.stdout.write(ex.stdout.decode())
                sys.stderr.write(ex.stderr.decode())
                raise Exception(f'Error processing repo {repo} branch {branch}') from ex

            sys.stdout.write(process.stdout.decode())
            sys.stderr.write(process.stderr.decode())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--update', action='store_true')
    parser.add_argument('--migrate', action='store_true')

    if argcomplete:
        argcomplete.autocomplete(parser)

    args = parser.parse_args()

    if args.update:
        update()

    if args.migrate:
        migrate()


if __name__ == '__main__':
    main()
