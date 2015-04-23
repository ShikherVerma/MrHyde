import git
import errno
from sqlite3 import Error as SQLError
import ConfigManager
from configparser import Error as ConfigError
import DbHandler
import logging

import string
import random
from time import time
from shutil import rmtree

from os.path import isdir, join, isfile
from os import makedirs, listdir, chdir, getcwd, mkdir
import subprocess


class RepositoryManager:
    __cm = None
    __db = None
    __base_dir = ""
    __git = git.Git()

    __logger = logging.getLogger(__name__)

    def __init__(self, config_file):
        try:
            self.__cm = ConfigManager.ConfigManager(config_file)
            self.__base_dir = self.__cm.get_base_dir()
            self.__base_url = self.__cm.get_base_url()
            self.__db = DbHandler.DbHandler(self.__cm.get_db_file())
        except ConfigError:
            raise
        except SQLError:
            raise

    def init_repository(self, url, diff):
        id = self.generateId(int(self.__cm.get_hash_size()))
        repo_path = join(self.__base_dir, id)
        deploy_path = ''.join([self.get_config().get_deploy_base_path(), id, self.get_config().get_deploy_append_path()])
        if not isdir(self.__base_dir):
            makedirs(self.__base_dir, 0o755, True)
        if not self.repository_exists(id):
            try:
                # TODO error handling
                self.__git.clone(url, repo_path)
                self.database().insertData('repo', id, repo_path, deploy_path, url, int(time()))
                if diff is not None and diff is not '':
                    self.apply_diff(id, diff)
                self.log().info('Repository cloned to ' + repo_path + '.')
                build_successful = self.start_jekyll_build(id)
                if build_successful:
                    return (id, ''.join(['https://', id, '.', self.get_config().get_base_url()]))
                else:
                    # TODO proper error handling!
                    raise Exception
            except OSError as exception:
                if exception.errno == errno.EPERM:
                    self.log().error("Permission to " + repo_path + " denied.")
                    raise
            except git.GitCommandError as exception:
                self.log().error(exception.__str__())
                raise
        else:
            return None

    def repository_exists(self, id):
        if isdir(join(self.__base_dir, id)):
            return True
        else:
            return False

    def list_repositories(self):
        dir_list = [[f['path'].split('/')[-1], f['url']] for f in self.database().list('repo') if isdir(f['path'])]
        return dir_list

    def list_single_directory(self, path):
        try:
            repo_path = ''.join([self.__base_dir, '/', path])
            file_list = [f for f in listdir(repo_path)]
            return file_list
        except OSError as exception:
            if exception.errno == errno.ENOENT:
                self.log().error("Repository " + repo_path + " doesn't exist.")
                raise
            elif exception.errno == errno.EPERM:
                self.log().error("Insufficient permissions to view " + repo_path + ".")
                raise
        except SQLError:
            self.log().error('Database error.')
            raise

    def delete_repository(self, id):
        try:
            repo = self.database().list('repo', '', "id='%s'" % id)[0]
            rmtree(repo['path'])
            self.database().deleteData('repo', "id='%s'" % repo['id'])
            return
        except OSError as exception:
            if exception.errno == errno.ENOENT:
                self.log().error('Repository ' + id + ' not found.')
                raise
            elif exception.errno == errno.EPERM:
                self.log().error('Insufficient permissions to remove repository ' + id + '.')
                raise
        except SQLError:
            raise

    def update_repository(self, id, diff):
        try:
            repo_path = self.database().list('repo', 'path', "id='%s'" % id)[0]
            diff_file = self.create_diff_file(id, diff)
            self.apply_diff(id, diff_file)
            build_successful = self.start_jekyll_build(repo_path)
            if build_successful:
                return ''.join(['https://', id, '.', self.__base_url])
            else:
                # TODO proper error handling!
                raise Exception
        except OSError as exception:
            if exception.errno == errno.ENOENT:
                self.log().error('Repository ' + repo_path['id'] + ' not found.')
                raise
            elif exception.errno == errno.EPERM:
                self.log().error('Insufficient permissions to remove repository ' + repo_path['id'] + '.')
                raise
        except SQLError:
            self.log().error('Database error.')
            raise
        except git.GitCommandError as exception:
            self.log().error(exception.__str__())
            raise

    def generateId(self, length=16, chars=string.ascii_lowercase+string.digits):
        return ''.join(random.SystemRandom().choice(chars) for _ in range(length))

    def start_jekyll_build(self, id):
        # TODO perhaps we should do this async (big pages?)
        pagepath = self.setup_deployment(id)
        path = self.database().list('repo', 'path', "id='%s'" % id)[0]
        cmd = ['jekyll', 'build', '--source', path, '--destination', pagepath]
        try:
            process = subprocess.check_output(cmd)
            return True
        except subprocess.CalledProcessError as exception:
            return False

    def get_config(self):
        return self.__cm

    def database(self):
        return self.__db

    def log(self):
        return self.__logger

    def apply_diff(self, id, diff):
        try:
            repo_path = self.database().list('repo', 'path', "id='%s'" % id)[0]
            old_dir = getcwd()
            chdir(repo_path)
            diff_file = self.create_diff_file(id, diff)
            self.__git.apply(diff_file)
            self.update_timestamp(id)
            chdir(old_dir)
        except SQLError:
            raise
        except git.GitCommandError:
            self.log().error('Unable to apply diff.')
            raise
        except OSError:
            raise

    def update_timestamp(self, id):
        self.database().updateData('repo', "id = '%s'" % id, 'last_used=%s' % int(time()))

    def create_diff_file(self, id, diff):
        try:
            file_name = ''.join(['/tmp/', id, '_patch.diff'])
            file_out = open(file_name, 'w')
        except IOError:
            self.log().error('Unable to create diff file.')
            raise
        else:
            file_out.write(diff)
            file_out.close()
            return file_name

    def file_download(self, id, file_name):
        repo_id = id.split('/')[0]
        file_path = id.split('/')[1:][0]

        try:
            repo_path = self.database().list('repo', 'path', "id='%s'" % repo_id)[0]
            abs_path = '/'.join([repo_path, ''.join(file_path), file_name])
            if isfile(abs_path):
                return True
            else:
                return False
        except OSError as exception:
            if exception.errno == errno.ENOENT:
                self.log().error('File' + file_path + ' not found.')
                raise
            elif exception.errno == errno.EPERM:
                self.log().error('Insufficient permissions to access file ' + file_path + '.')
                raise

    def setup_deployment(self, id):
        try:
            deploy_path = ''.join([self.get_config().get_deploy_base_path(), id, self.get_config().get_deploy_append_path()])
            makedirs(deploy_path, 0o755, True)
            self.log().info('Created deploy path ' + deploy_path)
            return deploy_path
        except OSError as exception:
            if exception.errno == errno.EPERM:
                self.log().error('Insufficient permissions to create directory: ' + deploy_path)
                return None

    def get_expiration_date(self, id):
        last_used = self.database().list('repo', 'last_used', "id='%s'" % id)[0]
        return last_used + (24 * 3600)
