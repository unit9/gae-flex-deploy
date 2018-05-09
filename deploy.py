#! /usr/bin/env python3

from settings import PROJECT_NAME

from yaml import dump, load
import argparse
import os
import subprocess
import shutil
from hashlib import sha256


def rreplace(s, old, new, occurrence=1):
    li = s.rsplit(old, occurrence)
    return new.join(li)

CONFIGS_BUCKET = 'gs://{}-configs/'.format(PROJECT_NAME)

current_directory = os.path.dirname(os.path.realpath(__file__))

parser = argparse.ArgumentParser(
    description='Deploy script, usage \n'
                'deploy.py environment --version default --app {} --appyaml app.dist.yaml'.format(PROJECT_NAME)
)
parser.add_argument('action', choices=['deploy', 'pull_config', 'push_config'],
                    help='what to do?')
parser.add_argument('environment', default='live',
                    help='env, looking for file env_{NAME}.conf in deploy directory')
parser.add_argument('--promote', default='no-promote',
                    help='promote')
parser.add_argument('--version', dest='version', default='default',
                    help='appengine flex env version')
parser.add_argument('--app', dest='app', default=PROJECT_NAME,
                    help='appengine flex env version')
parser.add_argument('--appyaml', dest='appyaml', default='app.dist.yaml',
                    help='base app yaml file default app.dist.yaml')
parser.add_argument('--force', '-f', action='store_true',
                    help='force config update')


try:
    with open('.cache', 'r') as f:
        cache = load(f)
except FileNotFoundError:
    with open('.cache', 'w') as f:
        cache = {}
        dump(cache, f)

print(cache)

args = parser.parse_args()
env_file = "{}.env".format(args.environment)
print(args)

# get full paths
env_file_path = os.path.join(current_directory, env_file)
appyaml_file_path = os.path.join(current_directory, args.appyaml)


def check_gsutil():
    try:
        subprocess.check_call(['gsutil', '--version'])
    except Exception as e:
        print("Failed to run gsutil: {}\n"
              "Make sure it's installed and working properly.".format(e))
        exit(1)


def check_bucket():

    params = ['gsutil', 'ls', '-p', PROJECT_NAME, CONFIGS_BUCKET]

    try:
        subprocess.check_output(params, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        print(e)
        if 'BucketNotFoundException' in e.output.decode():
            print('Bucket {} doesn\'t exist, creating...'
                  .format(CONFIGS_BUCKET))

            params = ['gsutil', 'mb', '-p', PROJECT_NAME, '-c', 'regional',
                      '-l', 'europe-west2', CONFIGS_BUCKET]

            subprocess.check_call(params)

            print('Created bucket {}'.format(CONFIGS_BUCKET))
        else:
            raise


def load_env(input_file):

    result = {}

    for line in input_file.readlines():

        key, value = line.split('=', 1)
        result[key.strip()] = value.strip()

    return result


def pull_config(env_file_path, env_file):
    check_gsutil()
    check_bucket()

    env_file_path_remote = env_file_path + '.remote'

    print("Downloading {} using gsutil..."
          .format(env_file))
    params = ['gsutil', 'cp', os.path.join(CONFIGS_BUCKET, env_file),
              env_file_path_remote]
    subprocess.check_call(params)

    with open(env_file_path_remote, 'r') as f:
        remote_sha = sha256(f.read().encode('utf-8')).hexdigest()

    try:
        with open(env_file, 'r') as f:
            local_sha = sha256(f.read().encode('utf-8')).hexdigest()

        if remote_sha == local_sha:
            print("No changes")
            cache[env_file] = local_sha
            return

        if env_file in cache and cache[env_file] == remote_sha:
            print("No new changes")
            return

        if env_file in cache and cache[env_file] != remote_sha and \
           cache[env_file] != local_sha:
            print("{} was modified both locally and on remote.\n"
                  "The remote config was retained in {}.\n"
                  "Merge it with local config and push using "
                  "'push_config -f'".format(env_file, env_file_path_remote))
            return
    except FileNotFoundError:
        print("Local {} doesn't exist.".format(env_file))

    shutil.move(env_file_path_remote, env_file_path)

    try:
        os.remove(env_file_path_remote)
    except OSError:
        pass

    cache[env_file] = remote_sha


def push_config(env_file_path, env_file):
    check_gsutil()

    env_file_path_remote = env_file_path + '.remote'

    try:
        with open(env_file, 'r') as f:
            local_sha = sha256(f.read().encode('utf-8')).hexdigest()
    except FileNotFoundError:
        print("Local {} doesn't exist.".format(env_file))
        exit(1)

    if not args.force:
        print("Downloading {} using gsutil..."
              .format(env_file))
        params = ['gsutil', 'cp', os.path.join(CONFIGS_BUCKET, env_file),
                  env_file_path_remote]
        subprocess.check_call(params)

        with open(env_file_path_remote, 'r') as f:
            remote_sha = sha256(f.read().encode('utf-8')).hexdigest()

        if remote_sha == local_sha:
            print("No changes")
            return

        if env_file in cache and cache[env_file] != remote_sha:
            print("{} was modified both locally and on remote.\n"
                  "The remote config was retained in {}.\n"
                  "Merge it with local config and push using "
                  "'push_config -f'".format(env_file, env_file_path_remote))
            return

    params = ['gsutil', 'cp', env_file_path,
              os.path.join(CONFIGS_BUCKET, env_file)]
    subprocess.check_call(params)

    try:
        os.remove(env_file_path_remote)
    except OSError:
        pass

    cache[env_file] = local_sha


def deploy(env_file_path, env_file):

    # checking if files exist

    if not os.path.isfile(appyaml_file_path):
        print("{} does not exist, please use default app.dist.yaml"
              .format(env_file_path))
        exit(1)

    # load files
    with open(os.path.join(current_directory, env_file), 'r') as file:
        env = load_env(file)

    with open(os.path.join(current_directory, args.appyaml), 'r') as file:
        appyaml = load(file)

    if 'env_variables' not in appyaml:
        appyaml['env_variables'] = env
    else:
        for key in env.keys():
            appyaml['env_variables'][key] = env[key]

    none_entry = False
    for key in appyaml['env_variables'].keys():
        if appyaml['env_variables'][key] is None and key not in env:
            print("Warning {} is set to null and not overwritten, ensure it's correct!".format(key))
            none_entry = True

    if none_entry and input("Continue? [Y/n]?") != 'Y':
        print("Quitting ...")
        exit()

    if 'service' in env and env['service'] != 'default':
        print("using custom service: {}".format(env['service']))
        appyaml['service'] = env['service']

    if 'version' in env:
        args.version = env['version']

    promote = True if args.promote == 'promote' else False

    # cuz of windows file access
    working_directory = rreplace(current_directory, 'deploy', '')
    app_file_path = os.path.join(current_directory, '..', 'app.yaml')

    try:
        print("dumping data to app.yaml")
        with open(app_file_path, 'w') as file:
            dump(appyaml, file, default_flow_style=False)
        print("running deployment, ensure gcloud SDK is configured")
        params = ['gcloud', 'app', 'deploy', '--project', args.app, '--quiet']

        if not promote:
            params.append('--no-promote')
            print("using --no-promote flag")
        if args.version != 'default':
            params.append('--version')
            params.append(args.version)
        print(params)
        subprocess.call(params, cwd=working_directory)
    finally:
        print("removing app files with secrets")
        os.remove(app_file_path)


if args.action == 'pull_config':
    pull_config(env_file_path, env_file)
elif args.action == 'push_config':
    push_config(env_file_path, env_file)
elif args.action == 'deploy':
    deploy(env_file_path, env_file)

with open('.cache', 'w') as f:
    dump(cache, f)

exit(0)
