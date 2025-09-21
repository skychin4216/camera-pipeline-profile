import subprocess
import re
from collections import defaultdict
import pandas as pd
import argparse
import os

import profile_util

class GitCommitAnalyzer:
    def __init__(self, repo_path):
        self.repo_path = repo_path
        self.logger = profile_util.set_logger('out.txt')
        self.logger.info(f"initilize GitCommitAnalyzer with log_file: {repo_path}")


    def get_git_log_commits(self, author_name):
        """获取指定作者的提交哈希列表"""
        try:
            # 切换到仓库目录
            original_dir = os.getcwd()
            os.chdir(self.repo_path) #该方法是一个用于改变当前工作目录的函数
            
            # 执行 git log 命令获取提交哈希
            cmd = ['git', 'log', '--author=' + author_name, '--pretty=format:%H']
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            # 切回原目录
            os.chdir(original_dir) 
            
            # 返回非空的提交哈希列表
            commits = [commit for commit in result.stdout.split('\n') if commit.strip()]
            return commits
        except subprocess.CalledProcessError as e:
            print(f"Error executing git log: {e}")
            return []
        except Exception as e:
            print(f"Unexpected error: {e}")
            return []

    def get_commit_files(self, commit_hash):
        """获取单个提交中修改的文件列表"""
        try:
            # 切换到仓库目录
            original_dir = os.getcwd()
            os.chdir(self.repo_path)
            
            # 执行 git show 命令获取文件列表
            cmd = ['git', 'show', '--pretty=format:', '--name-only', commit_hash]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            # 切回原目录
            os.chdir(original_dir)
            
            # 返回非空文件列表
            files = [f for f in result.stdout.split('\n') if f.strip()]
            return files
        except subprocess.CalledProcessError as e:
            print(f"Error executing git show for commit {commit_hash}: {e}")
            return []
        except Exception as e:
            print(f"Unexpected error for commit {commit_hash}: {e}")
            return []

    def analyze_author_files(self, author_name):
        """分析指定作者提交的文件"""
        print(f"正在获取作者 '{author_name}' 的提交记录...")
        commits = self.get_git_log_commits(author_name)
        
        if not commits:
            print("未找到相关提交记录")
            return None
        
        print(f"找到 {len(commits)} 个提交，正在分析文件修改情况...")
        
        # 统计文件出现次数
        file_count = defaultdict(int)
        
        for i, commit in enumerate(commits):
            print(f"处理提交 {i+1}/{len(commits)}: {commit[:8]}...")
            files = self.get_commit_files(commit)
            
            for file in files:
                file_count[file] += 1
        
        # 转换为DataFrame并按出现次数排序
        df = pd.DataFrame(list(file_count.items()), columns=['文件路径', '出现次数'])
        df = df.sort_values('出现次数', ascending=False).reset_index(drop=True)
        
        return df

    def analyze_core_files(self):
        parser = argparse.ArgumentParser(description='分析Git作者提交的文件统计')
        parser.add_argument('repo_path', help='Git仓库路径')
        parser.add_argument('author_name', help='作者姓名')
        parser.add_argument('--output', '-o', default='git_file_stats.xlsx', help='输出Excel文件名')

        self.logger.info(f"format parser args")
        args = parser.parse_args()
        self.logger.info(f"parser args for analyze_core_files done")

        if(self.repo_path == None) :
           self.repo_path = args.repo_path

        # 检查仓库路径是否存在
        if not os.path.exists(args.repo_path):
            print(f"错误：路径 '{args.repo_path}' 不存在")
            return

        # 分析文件
        result_df = self.analyze_author_files(args.author_name)
        
        if result_df is not None:
            # 保存到Excel
            result_df.to_excel(args.output, index=False)
            print(f"分析完成！结果已保存到 '{args.output}'")
            print(f"共统计了 {len(result_df)} 个文件")
            
            # 显示前10个最常修改的文件
            print("\n最常修改的前10个文件:")
            for i, row in result_df.head(10).iterrows():
                print(f"{i+1}. {row['文件路径']} - {row['出现次数']} 次")
        else:
            print("分析失败，请检查输入参数")

if __name__ == "__main__":
    log_file = "E:/workspace/openssCamera.log"
    print("check log_file: ", log_file)
    text_output = "pipeline_nodes_session.txt"  # 文本输出文件
    current_path = profile_util.get_current_directory(log_file)
    output_dir = f"{current_path}/profile_camera" # 输出目录
    print(f"output_dir {output_dir}")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    output_dir = os.path.join(current_path, f"profile_camera")
    os.makedirs(output_dir, exist_ok=True)
    print(f"output_dir {output_dir}")

    profile_util.clear_directory(output_dir)

    # 改变工作目录
    try:
        os.chdir(output_dir)
        print("成功切换到:", os.getcwd())
    except FileNotFoundError:
        print("路径不存在:", output_dir)
    except PermissionError:
        print("没有权限访问该路径:", output_dir)

    # 再次查看当前工作目录
    print("改变后的工作目录:", os.getcwd())

    # 初始化 git 代码提交分析器
    output_dir = None
    analyzer = GitCommitAnalyzer(output_dir)
    analyzer.analyze_core_files()


#pip install pandas openpyxl
#python git_author_analysis.py "E:\workspace\performance" "作者姓名"
#python git_author_analysis.py "E:\workspace\performance" "skychin4216@gmail.com"