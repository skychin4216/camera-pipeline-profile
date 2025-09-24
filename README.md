class ArgumentData:
    output : str = 'git_file_stats.xlsx'
    repo_path : str= 'E:\workspace\GimbalTrackingKit'
    author_name : str = "xxx@xxx.com"
    text_output : str = 'unique_files.txt'

    #below can improve a lot, may be timeout=30 or maybe -n 50
    cmd = ['git', 'log', '--author=' + author_name, '-n', '50', '--pretty=format:%H']
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)

        # 解析命令行参数
        if len(sys.argv) > 4: #sys.argv[0]是脚本名称，从sys.argv[1]开始是传递给脚本的参数
            print("输入的命令参数 : {len(sys.argv)}")
            args = parser.parse_args()
        else:
            print("使用默认的命令参数")
            args = ArgumentData()
            print(f"args.output: {args.output} args.repo_path: {args.repo_path} args.author_name:{args.author_name} args.text_output:{args.text_output}")
