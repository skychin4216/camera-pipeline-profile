import re
import os
import csv
import graphviz
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
from collections import defaultdict
from datetime import datetime, timedelta

import profile_util


class Node:
    def __init__(self, name, node_type):
        self.name = name
        self.type = node_type
        self.input_ports = set()
        self.output_ports = set()
        self.connections = []  # (source_port, dest_node, dest_port)

    def add_connection(self, source_port, dest_node, dest_port):
        self.output_ports.add(source_port)
        self.connections.append((source_port, dest_node, dest_port))


class Pipeline:
    def __init__(self, name, create_time, session_id):
        self.name = name
        self.session_id = session_id
        self.create_time = create_time
        self.activate_time = None
        self.deactivate_time = None
        self.streamon_time = None
        self.streamoff_time = None
        self.nodes = {}  # node_name: Node object
        self.is_active = False
        self.has_streamon = False
        self.camera_id = None

    def add_node(self, node_line):
        match = re.search(r'Node::(\w+) NodeName (\w+) Type (\d+)', node_line)
        if match:
            node_name = match.group(1)
            node_type = match.group(2)
            if node_name not in self.nodes:
                self.nodes[node_name] = Node(node_name, node_type)
            return node_name
        return None

    def add_link(self, link_line):
        # 解析连接关系
        source_match = re.search(r'Node::(\w+)\(outPort (\d+)\)', link_line)
        dest_match = re.search(r'--> \(inPort (\d+)\) Node::(\w+)', link_line)
        sink_match = re.search(r'--> \(Sink Buffer with temporary primary Format (\d+)\)', link_line)
        source_buffer_match = re.search(
            r'\(Source Buffer with temporary primary format (\d+)\) --> \(inPort (\d+)\) Node::(\w+)', link_line)

        if source_match and dest_match:
            src_node = source_match.group(1)
            src_port = source_match.group(2)
            dest_port = dest_match.group(1)
            dest_node = dest_match.group(2)

            if src_node in self.nodes and dest_node in self.nodes:
                self.nodes[src_node].add_connection(src_port, dest_node, dest_port)
                self.nodes[dest_node].input_ports.add(dest_port)
                return True

        elif sink_match and source_match:
            src_node = source_match.group(1)
            src_port = source_match.group(2)
            sink_name = f"SinkBufferFormat{sink_match.group(1)}"

            if sink_name not in self.nodes:
                self.nodes[sink_name] = Node(sink_name, "SinkBuffer")

            self.nodes[src_node].add_connection(src_port, sink_name, "0")
            self.nodes[sink_name].input_ports.add("0")
            return True

        elif source_buffer_match:
            buffer_name = f"SourceBufferFormat{source_buffer_match.group(1)}"
            dest_port = source_buffer_match.group(2)
            dest_node = source_buffer_match.group(3)

            if buffer_name not in self.nodes:
                self.nodes[buffer_name] = Node(buffer_name, "SourceBuffer")

            self.nodes[buffer_name].add_connection("0", dest_node, dest_port)
            self.nodes[dest_node].input_ports.add(dest_port)
            return True

        return False

    def generate_topology_dot(self, output_dir, active=True):
        dot = graphviz.Digraph(name=self.name, format='png')
        dot.attr(label=f"{self.name} ({'Active' if active else 'Inactive'})\nCreated: {self.create_time}")
        dot.attr('node', shape='box', style='filled')

        # 添加节点
        for node_name, node in self.nodes.items():
            if node.type in ["SinkBuffer", "SourceBuffer"]:
                dot.node(node_name, node_name, fillcolor='lightblue')
            else:
                dot.node(node_name, f"{node_name}\nType: {node.type}", fillcolor='lightgreen')

        # 添加连接
        for src_name, src_node in self.nodes.items():
            for src_port, dest_node, dest_port in src_node.connections:
                if dest_node in self.nodes:
                    dot.edge(src_name, dest_node,
                             label=f"{src_port}→{dest_port}",
                             color='blue' if active else 'gray',
                             penwidth='2' if active else '1')

        output_path = os.path.join(output_dir, f"{self.name}_{'active' if active else 'inactive'}")
        dot.render(output_path, cleanup=True)
        return f"{output_path}.png"


class Session:
    def __init__(self, session_id, start_time, camera_id):
        self.logger = profile_util.set_logger('out.txt')
        self.session_id = session_id
        self.start_time = start_time
        self.end_time = None
        self.camera_id = camera_id
        self.pipelines = {}  # pipeline_name: Pipeline object
        self.log_entries = []
        self.performance_data = []  # (node_name, port, request_id, start_ts, end_ts, duration)

    def add_log_entry(self, timestamp, line):
        self.log_entries.append((timestamp, line.strip()))

    def add_pipeline(self, pipeline_name, create_time):
        if pipeline_name not in self.pipelines:
            self.pipelines[pipeline_name] = Pipeline(pipeline_name, create_time, self.session_id)
        return self.pipelines[pipeline_name]

    def update_pipeline_state(self, pipeline_name, event_type, timestamp):
        if pipeline_name in self.pipelines:
            pipeline = self.pipelines[pipeline_name]
            if event_type == "ActivatePipeline":
                pipeline.activate_time = timestamp
                pipeline.is_active = True
            elif event_type == "DeActivatePipeline":
                pipeline.deactivate_time = timestamp
                pipeline.is_active = False
            elif event_type == "StreamOn":
                pipeline.streamon_time = timestamp
                pipeline.has_streamon = True
            elif event_type == "StreamOff":
                pipeline.streamoff_time = timestamp
                pipeline.has_streamon = False
            return True
        return False

    def add_performance_data(self, node_name, port, request_id, start_ts, end_ts):
        duration = (end_ts - start_ts).total_seconds() * 1000  # ms
        self.performance_data.append({
            'node': node_name,
            'port': port,
            'request_id': request_id,
            'start_time': start_ts,
            'end_time': end_ts,
            'duration': duration,
            'slow': duration > 25
        })
        return duration

    def get_performance_data(self, node_filter=None, port_filter=None):
        if node_filter is None and port_filter is None:
            return self.performance_data

        filtered = []
        for entry in self.performance_data:
            if node_filter and node_filter not in entry['node']:
                continue
            if port_filter and port_filter != entry['port']:
                continue
            filtered.append(entry)
        return filtered


class CameraLogAnalyzer:
    def __init__(self, log_file):
        self.log_file = log_file
        self.logger = profile_util.set_logger('out2.txt')
        self.sessions = {}  # session_id: Session object
        self.current_session = None
        self.session_counter = 1
        self.start_time = None
        self.performance_start_times = {}  # (node, port, request_id): timestamp

        # 编译正则表达式
        self.camera_open_patterns = [
            re.compile(r'openCamera : cameraId = (\d+)'),
            re.compile(r'openCamDevice E - cameraId (\d+)'),
            re.compile(r'CameraService::connect.*camera ID (\d+)'),
            re.compile(r'OpenCameraHardware_v3:.*name : (\d+)'),
            re.compile(r'uni_open_camera_hardware.*\[UNIKPI\]\[(\d+)\]')
        ]

        self.camera_close_patterns = [
            re.compile(r'closeCamera : cameraId = (\d+)'),
            re.compile(r'uni_close_camera_hardware.*\[UNIKPI\]\[(\d+)\]'),
            re.compile(r'camera3->close'),
            re.compile(r'flush')
        ]

        self.pipeline_create_pattern = re.compile(
            r'(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}).*camxpipeline\.cpp.*Creating Pipeline (\w+)'
        )

        self.node_create_pattern = re.compile(
            r'Topology::(\w+) Node::(\w+) NodeName (\w+) Type \d+'
        )

        self.link_pattern = re.compile(
            r'Topology: Pipeline\[(\w+)\].*Link: (.*)'
        )

        self.pipeline_event_patterns = {
            "ActivatePipeline": re.compile(r'ActivatePipeline.*name (\w+)'),
            "DeActivatePipeline": re.compile(r'DeActivatePipeline.*name (\w+)'),
            "StreamOn": re.compile(r'StreamOn.*\[(\w+)\] StreamingOn'),
            "StreamOff": re.compile(r'StreamOff.*(\w+) Streaming Off')
        }

        self.performance_patterns = {
            "ProcessCaptureRequest": re.compile(
                r'ProcessCaptureRequest.*Node::(\w+).*Port: (\d+).*SequenceId: \d+.*requestId: (\d+)'
            ),
            "ProcessFenceCallback": re.compile(
                r'ProcessFenceCallback.*Node::(\w+).*Port: (\d+).*SequenceId: \d+.*requestId: (\d+)'
            )
        }

        self.logger.info(f"initilize CameraLogAnalyzer with log_file: {log_file}")

    def parse_timestamp(self, ts_str, base_date=None):
        try:
            # 添加年份信息（假设是当前年）
            current_year = datetime.now().year
            full_ts = f"{current_year}-{ts_str}"
            dt = datetime.strptime(full_ts, "%Y-%m-%d %H:%M:%S.%f")

            # 如果是第一次解析时间戳，设置基准时间
            if self.start_time is None:
                self.start_time = dt
                base_date = dt

            # 如果日志跨天，调整日期
            if base_date and dt < base_date:
                dt += timedelta(days=1)

            return dt
        except Exception as e:
            print(f"Error parsing timestamp: {ts_str}, error: {e}")
            return None

    def extract_camera_id(self, line):
        for pattern in self.camera_open_patterns:
            match = pattern.search(line)
            if match:
                return match.group(1) if match.lastindex >= 1 else "unknown"
        return "unknown"

    def is_camera_open(self, line):
        return any(pattern.search(line) for pattern in self.camera_open_patterns)

    def is_camera_close(self, line):
        return any(pattern.search(line) for pattern in self.camera_close_patterns)

    def analyze(self):
        base_date = None
        current_session = None

        with open(self.log_file, 'r', encoding='utf-8') as file:
            for line in file:
                try:
                    # 解析时间戳
                    ts_match = re.match(r'(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})', line)
                    if not ts_match:
                        continue

                    ts_str = ts_match.group(1)
                    timestamp = self.parse_timestamp(ts_str, base_date)
                    base_date = timestamp

                    # 检查相机打开事件
                    if self.is_camera_open(line):
                        camera_id = self.extract_camera_id(line)
                        if not current_session or current_session.end_time:
                            # 创建新会话
                            session_id = f"session_{self.session_counter}"
                            self.session_counter += 1
                            current_session = Session(session_id, timestamp, camera_id)
                            self.sessions[session_id] = current_session

                    # 检查相机关闭事件
                    elif self.is_camera_close(line):
                        if current_session:
                            current_session.end_time = timestamp

                    # 处理会话中的日志
                    if current_session:
                        current_session.add_log_entry(timestamp, line)

                        # 检测流水线创建
                        pipeline_match = self.pipeline_create_pattern.search(line)
                        if pipeline_match:
                            pipeline_name = pipeline_match.group(2)
                            pipeline = current_session.add_pipeline(pipeline_name, timestamp)
                            pipeline.camera_id = current_session.camera_id

                        # 检测流水线事件（激活/停用等）
                        for event_type, pattern in self.pipeline_event_patterns.items():
                            event_match = pattern.search(line)
                            if event_match:
                                pipeline_name = event_match.group(1)
                                current_session.update_pipeline_state(pipeline_name, event_type, timestamp)

                        # 检测性能事件
                        for event_type, pattern in self.performance_patterns.items():
                            perf_match = pattern.search(line)
                            if perf_match:
                                node_name = perf_match.group(1)
                                port = perf_match.group(2)
                                request_id = perf_match.group(3)

                                key = (node_name, port, request_id)

                                if event_type == "ProcessCaptureRequest":
                                    self.performance_start_times[key] = timestamp
                                elif event_type == "ProcessFenceCallback":
                                    start_ts = self.performance_start_times.pop(key, None)
                                    if start_ts:
                                        current_session.add_performance_data(
                                            node_name, port, request_id, start_ts, timestamp
                                        )

                        # 解析节点和连接（如果当前行属于流水线拓扑）
                        if "camxpipeline.cpp" in line and ("CreateNodes()" in line or "Link:" in line):
                            # 节点创建
                            node_match = self.node_create_pattern.search(line)
                            if node_match:
                                pipeline_name = node_match.group(1)
                                if pipeline_name in current_session.pipelines:
                                    current_session.pipelines[pipeline_name].add_node(line)

                            # 连接关系
                            link_match = self.link_pattern.search(line)
                            if link_match:
                                pipeline_name = link_match.group(1)
                                if pipeline_name in current_session.pipelines:
                                    current_session.pipelines[pipeline_name].add_link(line)
                except Exception as e:
                    print(f"Error parsing timestamp: {ts_str}, error: {e}")

        print(f"Analysis completed. Found {len(self.sessions)} sessions.")

    def generate_topology_graphs(self, output_dir="topology"):
        os.makedirs(output_dir, exist_ok=True)

        for session_id, session in self.sessions.items():
            session_dir = os.path.join(output_dir, session_id)
            os.makedirs(session_dir, exist_ok=True)

            for pipeline_name, pipeline in session.pipelines.items():
                # 根据流水线状态决定生成哪种拓扑图
                if pipeline.has_streamon:
                    output_path = pipeline.generate_topology_dot(session_dir, active=True)
                    print(f"Generated active topology: {output_path}")
                else:
                    output_path = pipeline.generate_topology_dot(session_dir, active=False)
                    print(f"Generated inactive topology: {output_path}")

    def export_performance_data(self, output_file="node_times.csv"):
        all_data = []
        for session in self.sessions.values():
            for entry in session.performance_data:
                entry['session_id'] = session.session_id
                all_data.append(entry)

        if not all_data:
            print("No performance data found.")
            return

        df = pd.DataFrame(all_data)
        df.to_csv(output_file, index=False)
        print(f"Performance data exported to {output_file}")
        return df

    def search_node_performance(self, node_pattern, output_file=None):
        results = []
        for session in self.sessions.values():
            for entry in session.performance_data:
                if re.search(node_pattern, entry['node']):
                    results.append({
                        'session': session.session_id,
                        'node': entry['node'],
                        'port': entry['port'],
                        'request_id': entry['request_id'],
                        'duration': entry['duration'],
                        'slow': entry['slow']
                    })

        if not results:
            print(f"No results found for node pattern: {node_pattern}")
            return

        if output_file:
            with open(output_file, 'w') as f:
                writer = csv.DictWriter(f, fieldnames=results[0].keys())
                writer.writeheader()
                writer.writerows(results)
            print(f"Search results saved to {output_file}")

        return results

    def plot_session_timeline(self, output_file="session_timeline.png"):
        plt.figure(figsize=(15, 8))

        for i, (session_id, session) in enumerate(self.sessions.items()):
            # 绘制会话时间范围
            plt.hlines(y=i,
                       xmin=session.start_time,
                       xmax=session.end_time or datetime.now(),
                       linewidth=10,
                       color='blue')

            # 绘制流水线活动
            for j, (pipeline_name, pipeline) in enumerate(session.pipelines.items()):
                offset = j * 0.2
                if pipeline.streamon_time and pipeline.streamoff_time:
                    plt.hlines(y=i - 0.3 + offset,
                               xmin=pipeline.streamon_time,
                               xmax=pipeline.streamoff_time,
                               linewidth=6,
                               color='green' if pipeline.is_active else 'orange',
                               label=pipeline_name)

            plt.text(session.start_time, i, f"Cam {session.camera_id}",
                     verticalalignment='center', fontsize=9)

        plt.yticks(range(len(self.sessions)), list(self.sessions.keys()))
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        plt.gcf().autofmt_xdate()
        plt.title("Camera Sessions Timeline")
        plt.xlabel("Time")
        plt.ylabel("Session")
        plt.grid(True, axis='x')
        plt.tight_layout()
        plt.savefig(output_file)
        print(f"Session timeline saved to {output_file}")

    def plot_node_heatmap(self, output_file="node_heatmap.png"):
        # 收集所有节点的平均处理时间
        node_times = defaultdict(list)
        for session in self.sessions.values():
            for entry in session.performance_data:
                node_times[entry['node']].append(entry['duration'])

        if not node_times:
            print("No performance data available for heatmap")
            return

        # 创建数据框
        max_len = max(len(times) for times in node_times.values())
        data = {}
        for node, times in node_times.items():
            # 填充数据使所有列表等长
            padded = times + [0] * (max_len - len(times))
            data[node] = padded

        df = pd.DataFrame(data)

        # 绘制热力图
        plt.figure(figsize=(15, 10))
        sns.heatmap(df.T, cmap="YlOrRd", cbar_kws={'label': 'Processing Time (ms)'})
        plt.title("Node Processing Time Heatmap")
        plt.xlabel("Request Sequence")
        plt.ylabel("Node")
        plt.tight_layout()
        plt.savefig(output_file)
        print(f"Node heatmap saved to {output_file}")


# 使用示例
if __name__ == "__main__":
    # 初始化日志分析器
    log_file = "E:/workspace/openssCamera.log"
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
    analyzer.analyze()

    # 生成拓扑图
    analyzer.generate_topology_graphs()

    # 导出性能数据
    analyzer.export_performance_data()

    # 搜索特定节点性能
    analyzer.search_node_performance("IFE", output_file="ife_performance.txt")

    # 生成可视化
    analyzer.plot_session_timeline()
    analyzer.plot_node_heatmap()