import inspect
import os
import stat

def change_file_permissions(path):
    file_stat = os.stat(path)
    current_permissions = stat.filemode(file_stat.st_mode)
    print("Current permissions:", current_permissions)

    # 修改文件权限（添加写权限）
    os.chmod(path, stat.S_IWRITE)  # 或者使用其他合适的权限设置
    new_permissions = stat.filemode(os.stat(path).st_mode)
    print("New permissions:", new_permissions)

def get_current_directory(current_file):
    current_dir = os.path.dirname(os.path.abspath(current_file))
    #print(current_dir)
    return current_dir

def clear_directory(path):
    file_list = os.listdir(path)  # 获取目录下所有文件
    for file in file_list:
        file_path = os.path.join(path, file)  # 拼接文件路径
        if os.path.isfile(file_path):  # 判断是否为文件
            os.remove(file_path)  # 删除文件
        elif os.path.isdir(file_path):  # 判断是否为目录
            clear_directory(file_path)  # 递归清空子目录
            os.rmdir(file_path)  # 删除目录

import glob
import os
def clear_directory1(path):
    file_list = glob.glob(os.path.join(path, '*'))  # 获取目录下所有文件路径
    for file_path in file_list:
        change_file_permissions(file_path)
        os.remove(file_path)  # 删除文件, 会有权限问题


import shutil
def clear_directory2(path):
    shutil.rmtree(path)  # 递归删除目录和其所有内容
    os.mkdir(path)  # 重新创建空目录

import logging
def set_logger(output_log):
    # 配置日志记录器
    logger = logging.getLogger('my_logger')
    logger.setLevel(logging.DEBUG)

    # 使用 'with' 语句和 'open' 函数创建并写入文件
    with open(output_log, 'w') as file:
        file.write('This is log file\n')

    # 创建文件处理器和流处理器，并设置格式化器
    #formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s %(filename)s %(funcName)s %(lineno)d - %(message)s')
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(funcName)s %(lineno)d - %(message)s')

    # 创建一个handler，用于写入日志文件
    file_handler  = logging.FileHandler(output_log)
    file_handler .setLevel(logging.DEBUG)
    file_handler .setFormatter(formatter)

    # 创建一个流处理器，用于输出日志到控制台
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(formatter)

    # 将处理器添加到logger
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    # 使用logger记录日志
    logger.debug('这是一条debug信息')
    logger.info('这是一条info信息')
    logger.warning('这是一条warning信息')
    logger.error('这是一条error信息')
    logger.critical('这是一条critical信息')

    return logger