import re
import os
import graphviz
import csv
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from collections import defaultdict
import time
import warnings

import profile_util

# 禁用matplotlib的警告
warnings.filterwarnings("ignore", category=UserWarning, message=".*figure.max_open_warning.*")

class Pipeline:
    """表示一个相机处理流水线"""
    def __init__(self, name, session_id):
        self.name = name
        self.session_id = session_id
        self.nodes = {}  # 节点名称 -> 节点类型
        self.links = []  # 连接列表
        self.events = []  # (事件类型, 时间戳)
        self.streamon_time = None
        self.streamoff_time = None
        self.status = "unknown"  # 状态: on, off, active, inactive
    
    def add_node(self, node_name, node_type):
        """添加节点到流水线"""
        self.nodes[node_name] = node_type
    
    def add_link(self, source, source_port, target, target_port):
        """添加连接关系"""
        self.links.append({
            "source": source,
            "source_port": source_port,
            "target": target,
            "target_port": target_port
        })
    
    def add_event(self, event_type, timestamp):
        """添加事件（激活、停用、打开、关闭）"""
        self.events.append((event_type, timestamp))
        if event_type in ["on", "active"]:
            self.status = "active"
        elif event_type in ["off", "inactive"]:
            self.status = "inactive"
    
    def get_active_period(self):
        """获取流水线的活动时间段"""
        active_starts = [e[1] for e in self.events if e[0] in ["on", "active"]]
        active_ends = [e[1] for e in self.events if e[0] in ["off", "inactive"]]
        
        if active_starts and active_ends:
            active_start = min(active_starts)
            active_end = max(active_ends)
            return active_start, active_end
        return None, None

class Session:
    """表示一个相机会话（从打开到关闭）"""
    def __init__(self, session_id, start_time):
        self.id = session_id
        self.start_time = start_time
        self.end_time = None
        self.pipelines = {}  # 流水线名称 -> Pipeline对象
        self.camera_id = None  # 相机ID
    
    def add_pipeline(self, pipeline):
        """添加流水线到会话"""
        self.pipelines[pipeline.name] = pipeline
    
    def close(self, end_time):
        """关闭会话"""
        self.end_time = end_time
    
    def get_duration(self):
        """获取会话持续时间（秒）"""
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None
    
    def get_pipeline_count(self):
        """获取流水线数量"""
        return len(self.pipelines)

class CameraLogAnalyzer:
    def __init__(self, log_file):
        self.log_file = log_file
        self.sessions = {}  # session_id -> Session对象
        self.current_session = None
        self.session_count = 0
        self.start_time = None  # 日志开始时间
        self.log_events = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # pipeline -> (node, port, req_id) -> 事件列表

    def parse_log(self, chunk_size=100000):
        """解析日志文件，支持大文件分块处理"""
        print(f"开始解析日志文件: {self.log_file}")
        start_time = time.time()
        
        open_patterns = [
            r"openCamera : cameraId = (\d+)",
            r"openCamDevice E - cameraId (\d+)",
            r"CameraService::connect call.*camera ID (\d+)",
            r"OpenCameraHardware_v3:.*camera id : (\d+)",
            r"uni_open_camera_hardware:.*camera id : (\d+)",
            r"camera3->open.*cameraId (\d+)"
        ]
        close_patterns = [
            r"closeCamera : cameraId = (\d+)",
            r"CameraPerf: flush",
            r"VndHAL->flush:",
            r"flush\(\) HalOp:",
            r"uni_close_camera_hardware:",
            r"camera3->close"
        ]
        
        pipeline_creation_pattern = r"camxpipeline\.cpp.*?Pipeline\[(.*?)\]"
        node_pattern = r"Node::([\w_]+)\s+NodeName\s+(\w+)"
        link_pattern = (
            r"Link: (Node::([\w_]+)\(outPort\s+(\d+)\)|\(Source Buffer.*?format\s+(\d+)\)|\(Source Buffer\))"
            r".*?-->\s+"
            r"(Node::([\w_]+)\(inPort\s+(\d+)\)|\(Sink Buffer.*?format\s+(\d+)\)|\(Sink Buffer\))"
        )
        
        process_request_pattern = r"ProcessCaptureRequest.*?Node::([\w_]+).*?Port:\s+(\d+).*?requestId:\s+(\d+)"
        process_fence_pattern = r"ProcessFenceCallback.*?Node::([\w_]+).*?Port:\s+(\d+).*?requestId:\s+(\d+)"
        
        stream_on_pattern = r"StreamOn\(\) \[(.*?)\]"
        stream_off_pattern = r"StreamOff\(\) (.*?) Streaming Off"
        activate_pattern = r"ActivatePipeline\(\) (.*?),"
        deactivate_pattern = r"DeActivatePipeline\(\) (.*?)\s"

        # 记录解析进度
        line_count = 0
        chunk_count = 0
        
        with open(self.log_file, 'r', encoding='utf-8', errors='ignore') as f:
            chunk = []
            for line in f:
                line_count += 1
                chunk.append(line)
                
                # 处理日志块
                if len(chunk) >= chunk_size:
                    self._process_chunk(
                        chunk, open_patterns, close_patterns, pipeline_creation_pattern,
                        node_pattern, link_pattern, process_request_pattern, 
                        process_fence_pattern, stream_on_pattern, stream_off_pattern,
                        activate_pattern, deactivate_pattern
                    )
                    chunk = []
                    chunk_count += 1
                    print(f"已处理 {chunk_count * chunk_size} 行日志...")
            
            # 处理剩余的日志行
            if chunk:
                self._process_chunk(
                    chunk, open_patterns, close_patterns, pipeline_creation_pattern,
                    node_pattern, link_pattern, process_request_pattern, 
                    process_fence_pattern, stream_on_pattern, stream_off_pattern,
                    activate_pattern, deactivate_pattern
                )
        
        print(f"日志解析完成! 共处理 {line_count} 行日志, 耗时 {time.time()-start_time:.2f} 秒")
        print(f"发现 {len(self.sessions)} 个相机会话, {sum(len(session.pipelines) for session in self.sessions.values())} 个pipeline")

    def _process_chunk(self, chunk, open_patterns, close_patterns, pipeline_creation_pattern,
                     node_pattern, link_pattern, process_request_pattern, 
                     process_fence_pattern, stream_on_pattern, stream_off_pattern,
                     activate_pattern, deactivate_pattern):
        """处理日志块"""
        for line in chunk:
            # 检查相机打开/关闭
            camera_id = None
            for pattern in open_patterns:
                match = re.search(pattern, line)
                if match:
                    camera_id = match.group(1) if len(match.groups()) > 0 else None
                    break
            
            if camera_id:
                self.session_count += 1
                self.current_session = self.session_count
                timestamp = self._parse_timestamp(line)
                if not self.start_time:
                    self.start_time = timestamp
                
                session = Session(self.session_count, timestamp)
                session.camera_id = camera_id
                self.sessions[self.session_count] = session
            
            # 检查相机关闭
            closed = False
            for pattern in close_patterns:
                if re.search(pattern, line):
                    closed = True
                    break
            
            if closed and self.current_session:
                timestamp = self._parse_timestamp(line)
                if self.current_session in self.sessions:
                    self.sessions[self.current_session].close(timestamp)
                self.current_session = None
            
            # 只处理当前相机会话内的日志
            if not self.current_session:
                continue
            
            timestamp = self._parse_timestamp(line)
            current_session = self.sessions.get(self.current_session)
            
            # 解析pipeline创建
            pipeline_match = re.search(pipeline_creation_pattern, line)
            if pipeline_match:
                pipeline_name = pipeline_match.group(1)
                if pipeline_name not in current_session.pipelines:
                    pipeline = Pipeline(pipeline_name, self.current_session)
                    current_session.add_pipeline(pipeline)
            
            # 解析节点
            node_match = re.search(node_pattern, line)
            if node_match and current_session:
                pipeline_name = pipeline_match.group(1) if pipeline_match else None
                if pipeline_name and pipeline_name in current_session.pipelines:
                    node_name = node_match.group(1)
                    node_type = node_match.group(2)
                    current_session.pipelines[pipeline_name].add_node(node_name, node_type)
            
            # 解析连接
            link_match = re.search(link_pattern, line)
            if link_match and current_session:
                pipeline_name = pipeline_match.group(1) if pipeline_match else None
                if pipeline_name and pipeline_name in current_session.pipelines:
                    src_node = link_match.group(2) or (f"SourceBufferFormat{link_match.group(4)}" 
                                                      if link_match.group(4) else "SourceBuffer")
                    src_port = link_match.group(3) or "0"
                    dst_node = link_match.group(6) or (f"SinkBufferFormat{link_match.group(8)}" 
                                                     if link_match.group(8) else "SinkBuffer")
                    dst_port = link_match.group(7) or "0"
                    
                    current_session.pipelines[pipeline_name].add_link(
                        src_node, src_port, dst_node, dst_port
                    )
            
            # 解析处理请求和围栏回调
            request_match = re.search(process_request_pattern, line)
            fence_match = re.search(process_fence_pattern, line)
            
            if request_match:
                node, port, req_id = request_match.groups()
                timestamp = self._parse_timestamp(line)
                pipeline_name = pipeline_match.group(1) if pipeline_match else "unknown_pipeline"
                self.log_events[pipeline_name][(node, port, req_id)].append(("request", timestamp, line.strip()))
            
            if fence_match:
                node, port, req_id = fence_match.groups()
                timestamp = self._parse_timestamp(line)
                pipeline_name = pipeline_match.group(1) if pipeline_match else "unknown_pipeline"
                self.log_events[pipeline_name][(node, port, req_id)].append(("fence", timestamp, line.strip()))
            
            # 解析stream on/off
            stream_on_match = re.search(stream_on_pattern, line)
            if stream_on_match and current_session:
                pipeline_name = stream_on_match.group(1)
                if pipeline_name in current_session.pipelines:
                    current_session.pipelines[pipeline_name].add_event("on", timestamp)
            
            stream_off_match = re.search(stream_off_pattern, line)
            if stream_off_match and current_session:
                pipeline_name = stream_off_match.group(1)
                if pipeline_name in current_session.pipelines:
                    current_session.pipelines[pipeline_name].add_event("off", timestamp)
            
            # 解析激活/停用
            activate_match = re.search(activate_pattern, line)
            if activate_match and current_session:
                pipeline_name = activate_match.group(1)
                if pipeline_name in current_session.pipelines:
                    current_session.pipelines[pipeline_name].add_event("active", timestamp)
            
            deactivate_match = re.search(deactivate_pattern, line)
            if deactivate_match and current_session:
                pipeline_name = deactivate_match.group(1)
                if pipeline_name in current_session.pipelines:
                    current_session.pipelines[pipeline_name].add_event("inactive", timestamp)

    def _parse_timestamp(self, line):
        """解析时间戳，处理可能的格式变化"""
        try:
            # 尝试解析带日期的格式 "06-29 10:55:08.633"
            time_str = " ".join(line.split()[:2])
            return datetime.strptime(time_str, "%m-%d %H:%M:%S.%f")
        except ValueError:
            try:
                # 尝试解析不带日期的格式 "10:55:08.633"
                time_str = line.split()[1]
                return datetime.strptime(time_str, "%H:%M:%S.%f")
            except:
                # 如果都失败，返回当前时间
                return datetime.now()

    def export_pipeline_topology(self, output_dir="topology"):
        """导出所有流水线的拓扑图"""
        os.makedirs(output_dir, exist_ok=True)
        print(f"正在导出流水线拓扑到目录: {output_dir}")
        
        # 为每个session创建一个目录
        for session_id, session in self.sessions.items():
            session_dir = os.path.join(output_dir, f"session_{session_id}")
            os.makedirs(session_dir, exist_ok=True)
            
            for pipeline_name, pipeline in session.pipelines.items():
                print(f"处理流水线: {pipeline_name} (会话 {session_id})")
                
                # 保存为文本文件
                txt_path = os.path.join(session_dir, f"{pipeline_name}_topology.txt")
                with open(txt_path, 'w', encoding='utf-8', errors='ignore') as f:
                    f.write(f"Session: {session_id}\n")
                    f.write(f"Pipeline: {pipeline_name}\n")
                    f.write(f"Status: {pipeline.status}\n\n")
                    
                    f.write("Nodes:\n")
                    for node, node_type in pipeline.nodes.items():
                        f.write(f"  - {node} ({node_type})\n")
                    
                    f.write("\nConnections:\n")
                    for link in pipeline.links:
                        f.write(f"  {link['source']}:{link['source_port']} -> "
                                f"{link['target']}:{link['target_port']}\n")
                
                # 生成DOT图
                self._generate_dot_file(pipeline, session_dir)

    def _generate_dot_file(self, pipeline, output_dir):
        """使用Graphviz生成拓扑图"""
        try:
            # 节点颜色映射
            node_colors = {
                'Sensor': 'lightcoral',
                'IFE': 'lightgreen',
                'Stats': 'lightyellow',
                'AWB': 'plum',
                'StatsParse': 'lightsteelblue',
                'Tracker': 'lightblue',
                'SALINet': 'lightpink',
                'AutoFocus': 'lightsalmon',
                'IPE': 'lightseagreen',
                'FDManager': 'lightcyan',
                'com.qti.node.gyrornn': 'lightgoldenrodyellow'
            }
            
            # 创建有向图
            dot = graphviz.Digraph(
                name=pipeline.name,
                format='png',
                graph_attr={
                    'rankdir': 'LR',
                    'splines': 'ortho',
                    'label': f'Session: {pipeline.session_id}\nPipeline: {pipeline.name}\nStatus: {pipeline.status}',
                    'labelloc': 't',
                    'fontname': 'Helvetica',
                    'fontsize': '16'
                },
                node_attr={
                    'shape': 'box',
                    'style': 'filled,rounded',
                    'fontname': 'Helvetica'
                },
                edge_attr={
                    'fontname': 'Helvetica',
                    'fontsize': '10'
                }
            )
            
            # 添加所有节点
            for node_name, node_type in pipeline.nodes.items():
                # 获取节点颜色，如果未定义则使用默认颜色
                color = node_colors.get(node_type, 'lightgrey')
                
                # 创建节点标签
                label = f"{node_name}\n({node_type})"
                
                dot.node(
                    node_name, 
                    label=label,
                    fillcolor=color
                )
            
            # 添加特殊节点（源/汇）
            for link in pipeline.links:
                if link["source"].startswith("SourceBuffer"):
                    dot.node(
                        link["source"], 
                        link["source"],
                        shape='ellipse',
                        fillcolor='orange'
                    )
                if link["target"].startswith("SinkBuffer"):
                    dot.node(
                        link["target"], 
                        link["target"],
                        shape='ellipse',
                        fillcolor='gold'
                    )
            
            # 添加连接
            for link in pipeline.links:
                src = link["source"]
                dst = link["target"]
                label = f"{link['source_port']}→{link['target_port']}"
                
                # 设置连接颜色
                color = 'black'
                if src.startswith("SourceBuffer") or dst.startswith("SinkBuffer"):
                    color = 'blue'
                
                dot.edge(
                    src, 
                    dst,
                    xlabel=label,
                    color=color
                )
            
            # 渲染并清理
            output_path = os.path.join(output_dir, pipeline.name)
            dot.render(
                filename=output_path,
                cleanup=True
            )
            print(f"  生成拓扑图: {output_path}.png")
        except Exception as e:
            print(f"生成拓扑图时出错: {str(e)}")

    def calculate_node_times(self, output_file="node_times.csv"):
        """计算节点处理时间并保存到CSV"""
        print("计算节点处理时间...")
        with open(output_file, 'w', newline='', encoding='utf-8', errors='ignore') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                "SessionID", "Pipeline", "Node", "Port", "RequestID", 
                "StartTime", "EndTime", "Duration(ms)", "Status"
            ])
            
            processed_count = 0
            for pipeline_name, events in self.log_events.items():
                # 查找pipeline所属的session
                session_id = None
                for session in self.sessions.values():
                    if pipeline_name in session.pipelines:
                        session_id = session.id
                        break
                
                for (node, port, req_id), logs in events.items():
                    requests = [log for log in logs if log[0] == "request"]
                    fences = [log for log in logs if log[0] == "fence"]
                    
                    if not requests or not fences:
                        continue
                    
                    # 取最早请求和最晚围栏
                    request_time = min(log[1] for log in requests)
                    fence_time = max(log[1] for log in fences)
                    duration = (fence_time - request_time).total_seconds() * 1000
                    
                    status = "NORMAL"
                    if duration > 25:
                        status = "SLOW"
                    
                    writer.writerow([
                        session_id or "N/A",
                        pipeline_name, 
                        node, 
                        port, 
                        req_id,
                        request_time.strftime("%H:%M:%S.%f")[:-3],
                        fence_time.strftime("%H:%M:%S.%f")[:-3],
                        f"{duration:.2f}",
                        status
                    ])
                    processed_count += 1
            
            print(f"计算完成! 共处理 {processed_count} 个节点请求")

    def search_node_times(self, session_id=None, pipeline=None, node=None, port=None, 
                         output_file="search_results.txt"):
        """搜索节点处理时间"""
        print(f"搜索节点处理时间...")
        results = []
        with open(output_file, 'w', encoding='utf-8', errors='ignore') as f:
            for pipe, events in self.log_events.items():
                # 查找pipeline所属的session
                pipe_session_id = None
                for session in self.sessions.values():
                    if pipe in session.pipelines:
                        pipe_session_id = session.id
                        break
                
                # 会话过滤
                if session_id and pipe_session_id != session_id:
                    continue
                
                # 流水线过滤
                if pipeline and pipe != pipeline:
                    continue
                
                for (n, p, req_id), logs in events.items():
                    # 节点过滤
                    if node and n != node:
                        continue
                    
                    # 端口过滤
                    if port and p != port:
                        continue
                    
                    requests = [log for log in logs if log[0] == "request"]
                    fences = [log for log in logs if log[0] == "fence"]
                    
                    if not requests or not fences:
                        continue
                    
                    request_time = min(log[1] for log in requests)
                    fence_time = max(log[1] for log in fences)
                    duration = (fence_time - request_time).total_seconds() * 1000
                    
                    result_line = (
                        f"Session: {pipe_session_id or 'N/A'}, "
                        f"Pipeline: {pipe}, "
                        f"Node: {n}, "
                        f"Port: {p}, "
                        f"Request: {req_id}, "
                        f"Start: {request_time.strftime('%H:%M:%S.%f')[:-3]}, "
                        f"End: {fence_time.strftime('%H:%M:%S.%f')[:-3]}, "
                        f"Duration: {duration:.2f}ms"
                    )
                    
                    if duration > 25:
                        result_line = "    !!! " + result_line  # 标记超时
                    
                    results.append((duration, result_line))
            
            # 按持续时间排序
            results.sort(key=lambda x: x[0], reverse=True)
            
            # 写入结果
            if not results:
                f.write("未找到匹配的节点处理时间记录\n")
            else:
                f.write(f"找到 {len(results)} 条匹配记录:\n")
                f.write("=" * 150 + "\n")
                for _, line in results:
                    f.write(line + "\n")
            
            print(f"搜索结果已保存到 {output_file}")

    def generate_report(self, report_file="camera_log_report.txt"):
        """生成综合报告"""
        print("生成综合报告...")
        with open(report_file, 'w', encoding='utf-8', errors='ignore') as f:
            # 报告头
            f.write("Android Camera 日志分析报告\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"日志文件: {self.log_file}\n")
            f.write(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S') if self.start_time else 'N/A'}\n\n")
            
            # 会话报告
            f.write("相机会话概览:\n")
            f.write("-" * 80 + "\n")
            f.write(f"{'ID':<5}{'相机ID':<8}{'开始时间':<20}{'结束时间':<20}{'持续时间(s)':<12}{'流水线数量':<12}\n")
            for session_id, session in self.sessions.items():
                duration = session.get_duration() or "运行中"
                f.write(f"{session_id:<5}{session.camera_id or 'N/A':<8}")
                f.write(f"{session.start_time.strftime('%H:%M:%S.%f')[:-3]:<20}")
                f.write(f"{session.end_time.strftime('%H:%M:%S.%f')[:-3] if session.end_time else 'N/A':<20}")
                f.write(f"{duration if isinstance(duration, str) else f'{duration:.2f}':<12}")
                f.write(f"{len(session.pipelines):<12}\n")
            f.write("\n")
            
            # 流水线报告
            f.write("流水线状态概览:\n")
            f.write("-" * 80 + "\n")
            f.write(f"{'会话ID':<8}{'流水线名称':<40}{'状态':<12}{'节点数':<8}{'连接数':<8}{'活动时间':<20}\n")
            for session_id, session in self.sessions.items():
                for pipeline_name, pipeline in session.pipelines.items():
                    start_time, end_time = pipeline.get_active_period()
                    active_time = "N/A"
                    if start_time and end_time:
                        active_time = f"{start_time.strftime('%H:%M:%S')}-{end_time.strftime('%H:%M:%S')}"
                    
                    f.write(f"{session_id:<8}{pipeline_name:<40}{pipeline.status:<12}")
                    f.write(f"{len(pipeline.nodes):<8}{len(pipeline.links):<8}{active_time:<20}\n")
            f.write("\n")
            
            # 性能摘要
            slow_nodes = defaultdict(int)
            total_nodes = 0
            
            for pipeline_name, events in self.log_events.items():
                for key, logs in events.items():
                    requests = [log for log in logs if log[0] == "request"]
                    fences = [log for log in logs if log[0] == "fence"]
                    
                    if requests and fences:
                        request_time = min(log[1] for log in requests)
                        fence_time = max(log[1] for log in fences)
                        duration = (fence_time - request_time).total_seconds() * 1000
                        
                        if duration > 25:
                            node = key[0]
                            slow_nodes[node] += 1
                        total_nodes += 1
            
            if total_nodes > 0:
                f.write("性能问题摘要:\n")
                f.write("-" * 80 + "\n")
                f.write(f"总节点请求数: {total_nodes}\n")
                f.write(f"超时节点请求数: {sum(slow_nodes.values())} ({sum(slow_nodes.values())/total_nodes*100:.1f}%)\n\n")
                
                if slow_nodes:
                    f.write("超时最多的节点:\n")
                    for node, count in sorted(slow_nodes.items(), key=lambda x: x[1], reverse=True)[:5]:
                        f.write(f"  - {node}: {count} 次超时\n")
                else:
                    f.write("未检测到超时节点\n")
            else:
                f.write("无节点性能数据可用\n")
            
            f.write("\n报告结束\n")
            print(f"报告已保存到 {report_file}")

    def plot_session_timeline(self, output_file="session_timeline.png"):
        """绘制会话时间线图"""
        if not self.sessions:
            print("无相机会话数据，无法生成时间线")
            return
        
        print("生成会话时间线图...")
        plt.figure(figsize=(14, 8))
        
        # 创建时间轴
        min_time = min(session.start_time for session in self.sessions.values())
        max_time = max(session.end_time for session in self.sessions.values() if session.end_time) or datetime.now()
        
        # 绘制会话
        session_list = list(self.sessions.values())
        for i, session in enumerate(session_list):
            start = session.start_time
            end = session.end_time or datetime.now()
            
            # 主会话条
            plt.barh(
                y=i, 
                width=(end - start).total_seconds(),
                left=start,
                height=0.6,
                color='skyblue',
                edgecolor='navy',
                alpha=0.7
            )
            
            # 标注会话信息
            session_label = f"Session {session.id} (Camera {session.camera_id})"
            plt.text(
                x=start + (end - start)/2, 
                y=i,
                s=session_label,
                ha='center',
                va='center',
                fontsize=9,
                fontweight='bold'
            )
            
            # 绘制pipeline活动
            pipeline_y = i - 0.2
            for pipeline_name, pipeline in session.pipelines.items():
                events = pipeline.events
                if not events:
                    continue
                
                # 计算pipeline的活动时间段
                active_start, active_end = pipeline.get_active_period()
                if active_start and active_end:
                    # pipeline活动条
                    plt.barh(
                        y=pipeline_y,
                        width=(active_end - active_start).total_seconds(),
                        left=active_start,
                        height=0.3,
                        color='limegreen',
                        edgecolor='darkgreen',
                        alpha=0.8
                    )
                    
                    # 标注pipeline名称
                    plt.text(
                        x=active_start + (active_end - active_start)/2,
                        y=pipeline_y,
                        s=pipeline_name,
                        ha='center',
                        va='center',
                        fontsize=7,
                        rotation=30
                    )
                    
                    pipeline_y -= 0.2
        
        # 设置Y轴
        plt.yticks(
            ticks=range(len(session_list)),
            labels=[f"Session {s.id}" for s in session_list]
        )
        plt.ylabel("相机会话")
        
        # 设置X轴（时间）
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        plt.gca().xaxis.set_major_locator(mdates.SecondLocator(interval=30))
        plt.gcf().autofmt_xdate()
        plt.xlabel("时间")
        
        plt.title("相机会话与流水线活动时间线")
        plt.grid(axis='x', linestyle='--', alpha=0.6)
        plt.tight_layout()
        
        plt.savefig(output_file, dpi=150)
        plt.close()
        print(f"时间线图已保存到 {output_file}")

    def plot_node_heatmap(self, output_file="node_heatmap.png"):
        """绘制节点性能热力图"""
        if not self.log_events:
            print("无节点性能数据，无法生成热力图")
            return
        
        print("生成节点性能热力图...")
        # 收集节点性能数据
        node_durations = defaultdict(list)
        
        for pipeline_name, events in self.log_events.items():
            for (node, port, req_id), logs in events.items():
                requests = [log for log in logs if log[0] == "request"]
                fences = [log for log in logs if log[0] == "fence"]
                
                if not requests or not fences:
                    continue
                
                request_time = min(log[1] for log in requests)
                fence_time = max(log[1] for log in fences)
                duration = (fence_time - request_time).total_seconds() * 1000
                node_durations[node].append(duration)
        
        if not node_durations:
            print("无有效的节点性能数据")
            return
        
        # 准备热力图数据
        nodes = []
        avg_times = []
        max_times = []
        min_times = []
        slow_counts = []
        
        for node, durations in node_durations.items():
            nodes.append(node)
            avg_times.append(np.mean(durations))
            max_times.append(np.max(durations))
            min_times.append(np.min(durations))
            slow_counts.append(sum(1 for d in durations if d > 25))
        
        # 创建图表
        fig, ax = plt.subplots(figsize=(14, 10))
        
        # 节点性能条
        y_pos = np.arange(len(nodes))
        bar_width = 0.35
        
        # 平均时间条
        avg_bars = ax.barh(
            y_pos - bar_width/2, 
            avg_times, 
            bar_width, 
            color='skyblue',
            label='平均处理时间'
        )
        
        # 最大时间条
        max_bars = ax.barh(
            y_pos + bar_width/2, 
            max_times, 
            bar_width, 
            color='salmon',
            label='最大处理时间'
        )
        
        # 添加超时计数文本
        for i, count in enumerate(slow_counts):
            if count > 0:
                ax.text(
                    max_times[i] + 2, 
                    i + bar_width/2,
                    f"{count}次超时",
                    va='center',
                    fontsize=9,
                    color='red'
                )
        
        # 设置Y轴
        ax.set_yticks(y_pos)
        ax.set_yticklabels(nodes)
        ax.invert_yaxis()  # 从上到下显示
        
        # 设置X轴
        ax.set_xlabel('处理时间 (ms)')
        ax.set_title('节点性能热力图')
        
        # 添加参考线
        ax.axvline(x=25, color='r', linestyle='--', alpha=0.7)
        ax.text(
            25 + 1, 
            len(nodes) - 1,
            '超时阈值 (25ms)',
            color='red',
            va='center'
        )
        
        # 添加图例
        ax.legend()
        
        # 添加数值标签
        for bar in avg_bars:
            width = bar.get_width()
            ax.text(
                width + 0.5,
                bar.get_y() + bar.get_height()/2,
                f'{width:.1f}',
                ha='left',
                va='center',
                fontsize=8
            )
        
        plt.tight_layout()
        plt.savefig(output_file, dpi=150)
        plt.close()
        print(f"热力图已保存到 {output_file}")

def main():
    # 初始化日志分析器
    log_file = "E:/workspace/openssCamera2.log"
    text_output = "pipeline_nodes_session.txt"  # 文本输出文件
    current_path = profile_util.get_current_directory(log_file)
    output_dir = f"{current_path}/profile_camerax" # 输出目录
    print(f"output_dir {output_dir}")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    output_dir = os.path.join(current_path, f"profile_camerax")
    os.makedirs(output_dir, exist_ok=True)
    print(f"output_dir {output_dir}")

    profile_util.clear_directory1(output_dir)

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

    analyzer = CameraLogAnalyzer(log_file)
    
    # 解析日志（支持大文件）
    analyzer.parse_log()
    
    # 导出流水线拓扑
    analyzer.export_pipeline_topology()
    
    # 计算节点处理时间
    analyzer.calculate_node_times()
    
    # 生成报告
    analyzer.generate_report()
    
    # 生成可视化
    analyzer.plot_session_timeline()
    analyzer.plot_node_heatmap()
    
    # 示例搜索
    print("\n运行示例搜索...")
    analyzer.search_node_times(
        session_id=1,
        node="IFE", 
        output_file="session1_ife_times.txt"
    )
    
    print("分析完成! 所有结果已保存到当前目录")

if __name__ == "__main__":
    main()