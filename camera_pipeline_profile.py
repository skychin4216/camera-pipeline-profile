#帮我写一个代码：从一个 android camera log base on latest 0804
import re
import os
import subprocess
from collections import defaultdict, deque
import datetime
from graphviz import Digraph

import profile_util

class CameraLogAnalyzer:
    def __init__(self, log_file):
        self.log_file = log_file
        self.logger = profile_util.set_logger('out3.txt')
        self.pipeline_data = defaultdict(
            lambda: {
                'nodes': defaultdict(
                    lambda: {
                        'output_ports': {},  # 存储端口号到信息的映射
                        'type': '',
                        'input_ports': 0,
                        'output_ports_count': 0,
                        'log_entries': []  # 存储包含该节点的日志条目和时间戳
                    }
                ),
                'creation_time': None,
                'session_id': None,  # 所属会话ID
                'camera_id': None,  # 所属相机ID
                'stream_state': 'off',  # stream状态: on/off
                'stream_on_time': None,  # 最后stream on时间
                'stream_off_time': None,  # 最后stream off时间
            }
        )
        self.all_log_entries = []  # 存储所有日志条目和时间戳
        self.sessions = {}  # 存储会话信息
        self.current_session = None  # 当前活跃会话
        self.session_counter = 1  # 会话计数器
        self.camera_connections = defaultdict(set)  # 相机连接关系
        self._parse_log()
        self._analyze_connections()
        self.logger.info(f"initilize CameraLogAnalyzer with log_file: {log_file}")

    def _timestamp_to_ms(self, timestamp_str):
        """将时间戳字符串转换为毫秒时间戳"""
        try:
            # 解析时间字符串：06-21 22:12:43.090
            parts = timestamp_str.split()
            date_part = parts[0]
            time_part = parts[1]
            month, day = map(int, date_part.split('-'))
            hour, minute, second_millis = time_part.split(':')
            second, millisecond = second_millis.split('.')

            # 创建时间对象（年份使用当前年，因为日志中不包含年份）
            dt = datetime.datetime(
                datetime.datetime.now().year, month, day,
                int(hour), int(minute), int(second), int(millisecond) * 1000
            )

            # 转换为毫秒时间戳
            return int(dt.timestamp() * 1000) + int(millisecond)
        except Exception as e:
            self.logger.info(f"Error parsing timestamp: {timestamp_str} - {e}")
            return 0

    def _parse_log(self):
        # 正则表达式模式
        timestamp_pattern = r'^(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})'
        pipeline_start_pattern = r'CreateDescriptor\(\) Pipeline\[(\w+)\].*?pipeline pointer \w+'
        pipeline_node_pattern = r'CreateNodes\(\) Topology::(\w+_\d+_cam_\d+).*?Node::(\w+)\s+NodeName\s+(\w+)\s+Type\s+(\d+).*?numInputPorts\s+(\d+).*?numOutputPorts\s+(\d+)'
        node_link_pattern = r'Topology: Pipeline\[(\w+_\d+_cam_\d+)\]\s+Link: Node::(\w+)\(outPort\s+(\d+)\).*?-->\s+\(inPort\s+(\d+)\).*?Node::(\w+)'
        sink_link_pattern = r'Topology: Pipeline\[(\w+_\d+_cam_\d+)\]\s+Link: Node::(\w+)\(outPort\s+(\d+)\).*?-->\s+\(Sink Buffer with temporary primary Format (\d+)\)'
        source_link_pattern = r'Topology: Pipeline\[(\w+_\d+_cam_\d+)\]\s+Link: \(Source Buffer with temporary primary format (\d+)\)\s+-->\s+\(inPort (\d+)\).*?Node::(\w+)'
        node_in_log_pattern = r'Node::?([\w]+)'

        # 会话相关模式
        session_start_patterns = [
            r'OpenCamera E',
            r'CameraService::connect',
            r'camera3->open',
            r'configure_streams'
        ]
        session_end_patterns = [
            r'closeCamera',
            r'VndHAL->flush:',
            r'camera3->close',
            r'flush E'
        ]

        # stream状态模式
        stream_on_pattern = r'camxpipeline.cpp:\d+ StreamOn\(\) \[(\w+_\d+_cam_\d+)\]'
        stream_off_pattern = r'camxpipeline.cpp:\d+ StreamOff\(\) (\w+_\d+_cam_\d+)'

        # 相机ID提取模式
        camera_id_pattern = r'camera ID (\d+)'

        current_pipeline = None

        with open(self.log_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                # 提取时间戳
                timestamp_match = re.match(timestamp_pattern, line)
                timestamp_str = timestamp_match.group(1) if timestamp_match else ""
                timestamp_ms = self._timestamp_to_ms(timestamp_str) if timestamp_str else 0

                # 存储日志条目
                log_entry = {
                    'timestamp': timestamp_ms,
                    'content': line.strip(),
                    'timestamp_str': timestamp_str
                }
                self.all_log_entries.append(log_entry)

                if self.current_session == None:
                    # 检查会话开始事件
                    session_started = False
                    for pattern in session_start_patterns:
                        if re.search(pattern, line):
                            # 提取相机ID
                            cam_id_match = re.search(camera_id_pattern, line)
                            camera_id = cam_id_match.group(1) if cam_id_match else "unknown"

                            # 创建新会话
                            session_id = f"session_{self.session_counter}"
                            self.session_counter += 1
                            self.current_session = session_id
                            self.sessions[session_id] = {
                                'start_time': timestamp_ms,
                                'end_time': None,
                                'camera_id': camera_id,
                                'pipelines': set(),
                                'log_entries': [log_entry]
                            }
                            session_started = True
                            self.logger.info(f"Session started: {session_id} for camera {camera_id}")
                            break

                # 检查会话结束事件
                if session_started:
                    for pattern in session_end_patterns:
                        if re.search(pattern, line):
                            if self.current_session:
                                self.sessions[self.current_session]['end_time'] = timestamp_ms
                                self.sessions[self.current_session]['log_entries'].append(log_entry)
                                self.logger.info(f"Session ended: {self.current_session}")
                                self.current_session = None
                            break

                # 检查stream on/off事件
                stream_on_match = re.search(stream_on_pattern, line)
                if stream_on_match:
                    pipeline_name = stream_on_match.group(1)
                    if pipeline_name in self.pipeline_data:
                        self.pipeline_data[pipeline_name]['stream_state'] = 'on'
                        self.pipeline_data[pipeline_name]['stream_on_time'] = timestamp_ms
                        # 关联当前会话
                        if self.current_session:
                            self.sessions[self.current_session]['pipelines'].add(pipeline_name)
                            self.pipeline_data[pipeline_name]['session_id'] = self.current_session
                            self.pipeline_data[pipeline_name]['camera_id'] = self.sessions[self.current_session]['camera_id']

                stream_off_match = re.search(stream_off_pattern, line)
                if stream_off_match:
                    pipeline_name = stream_off_match.group(1)
                    if pipeline_name in self.pipeline_data:
                        self.pipeline_data[pipeline_name]['stream_state'] = 'off'
                        self.pipeline_data[pipeline_name]['stream_off_time'] = timestamp_ms

                # 匹配Pipeline起始行
                pipeline_start = re.search(pipeline_start_pattern, line)
                if pipeline_start:
                    current_pipeline = pipeline_start.group(1)
                    self.pipeline_data[current_pipeline]['creation_time'] = timestamp_ms
                    # 关联当前会话
                    if self.current_session:
                        self.sessions[self.current_session]['pipelines'].add(current_pipeline)
                        self.pipeline_data[current_pipeline]['session_id'] = self.current_session
                        self.pipeline_data[current_pipeline]['camera_id'] = self.sessions[self.current_session]['camera_id']
                    continue

                # 匹配Node行
                node_match = re.search(pipeline_node_pattern, line)
                if node_match:
                    pipeline_name, node_name, node_type, _, in_ports, out_ports = node_match.groups()
                    self.pipeline_data[pipeline_name]['nodes'][node_name]['type'] = node_type
                    self.pipeline_data[pipeline_name]['nodes'][node_name]['input_ports'] = int(in_ports)
                    self.pipeline_data[pipeline_name]['nodes'][node_name]['output_ports_count'] = int(out_ports)

                    # 记录节点日志条目
                    self.pipeline_data[pipeline_name]['nodes'][node_name]['log_entries'].append(log_entry)
                    continue

                # 匹配节点到节点的连接
                node_link_match = re.search(node_link_pattern, line)
                if node_link_match:
                    pipeline_name, src_node, src_port, dst_port, dst_node = node_link_match.groups()

                    # 为输出端口创建条目
                    port_info = self.pipeline_data[pipeline_name]['nodes'][src_node]['output_ports']
                    if src_port not in port_info:
                        port_info[src_port] = {
                            'connected_to': [],
                            'info': f"Output port {src_port} of {src_node}",
                            'log_entry': log_entry
                        }

                    # 添加连接目标
                    port_info[src_port]['connected_to'].append({
                        'node': dst_node,
                        'port': dst_port,
                        'type': 'node'
                    })
                    continue

                # 匹配节点到Sink Buffer的连接
                sink_link_match = re.search(sink_link_pattern, line)
                if sink_link_match:
                    pipeline_name, src_node, src_port, format_num = sink_link_match.groups()
                    sink_node = f"SinkBufferFormat{format_num}"

                    # 为输出端口创建条目
                    port_info = self.pipeline_data[pipeline_name]['nodes'][src_node]['output_ports']
                    if src_port not in port_info:
                        port_info[src_port] = {
                            'connected_to': [],
                            'info': f"Output port {src_port} of {src_node}",
                            'log_entry': log_entry
                        }

                    # 添加Sink Buffer连接
                    port_info[src_port]['connected_to'].append({
                        'node': sink_node,
                        'port': f"Format{format_num}",
                        'type': 'sink'
                    })

                    # 确保Sink Buffer节点存在
                    if sink_node not in self.pipeline_data[pipeline_name]['nodes']:
                        self.pipeline_data[pipeline_name]['nodes'][sink_node] = {
                            'output_ports': {},
                            'type': 'SinkBuffer',
                            'input_ports': 1,
                            'output_ports_count': 0,
                            'log_entries': []
                        }
                    continue

                # 匹配Source Buffer到节点的连接
                source_link_match = re.search(source_link_pattern, line)
                if source_link_match:
                    pipeline_name, format_num, in_port, dst_node = source_link_match.groups()
                    source_node = f"SourceBufferFormat{format_num}"

                    # 确保Source Buffer节点存在
                    if source_node not in self.pipeline_data[pipeline_name]['nodes']:
                        self.pipeline_data[pipeline_name]['nodes'][source_node] = {
                            'output_ports': {},
                            'type': 'SourceBuffer',
                            'input_ports': 0,
                            'output_ports_count': 1,
                            'log_entries': []
                        }

                    # 为Source Buffer添加输出端口
                    port_info = self.pipeline_data[pipeline_name]['nodes'][source_node]['output_ports']
                    if "0" not in port_info:
                        port_info["0"] = {
                            'connected_to': [],
                            'info': f"Output port 0 of {source_node}",
                            'log_entry': log_entry
                        }

                    # 添加连接目标
                    port_info["0"]['connected_to'].append({
                        'node': dst_node,
                        'port': in_port,
                        'type': 'source'
                    })
                    continue

                # 记录包含节点的其他日志条目
                node_in_log = re.search(node_in_log_pattern, line)
                if node_in_log:
                    node_name = node_in_log.group(1)
                    for pipeline, data in self.pipeline_data.items():
                        if node_name in data['nodes']:
                            data['nodes'][node_name]['log_entries'].append(log_entry)

    def _analyze_connections(self):
        """分析pipeline之间的连接关系"""
        # 收集所有SinkBuffer和SourceBuffer
        sink_buffers = defaultdict(list)  # format -> (pipeline, node)
        source_buffers = defaultdict(list)  # format -> (pipeline, node)

        # 第一轮遍历：收集所有buffer节点
        for pipeline, data in self.pipeline_data.items():
            for node, node_data in data['nodes'].items():
                if node_data['type'] == 'SinkBuffer':
                    # 提取格式号 (SinkBufferFormat3 -> 3)
                    if match := re.search(r'SinkBufferFormat(\d+)', node):
                        format_num = match.group(1)
                        sink_buffers[format_num].append((pipeline, node))

                if node_data['type'] == 'SourceBuffer':
                    # 提取格式号 (SourceBufferFormat3 -> 3)
                    if match := re.search(r'SourceBufferFormat(\d+)', node):
                        format_num = match.group(1)
                        source_buffers[format_num].append((pipeline, node))

        # 第二轮遍历：建立pipeline之间的连接
        for format_num, sinks in sink_buffers.items():
            sources = source_buffers.get(format_num, [])

            for sink_pipeline, sink_node in sinks:
                for source_pipeline, source_node in sources:
                    # 避免自连接
                    if sink_pipeline != source_pipeline:
                        # 记录连接关系
                        self.camera_connections[sink_pipeline].add(source_pipeline)
                        self.camera_connections[source_pipeline].add(sink_pipeline)

                        # 打印连接信息
                        self.logger.info(f"Discovered connection: {source_pipeline} -> {sink_pipeline} via Format {format_num}")

    def get_port_info(self, pipeline, node, port):
        """获取特定端口的详细信息"""
        try:
            port_info = self.pipeline_data[pipeline]['nodes'][node]['output_ports'][port]
            # 添加额外信息
            port_info['full_path'] = f"{pipeline}::{node}::{port}"
            port_info['node_type'] = self.pipeline_data[pipeline]['nodes'][node]['type']
            port_info['total_ports'] = self.pipeline_data[pipeline]['nodes'][node]['output_ports_count']
            return port_info
        except KeyError:
            return None

    def find_time_diff_between_node_logs(self, node_name, pattern1, pattern2):
        """计算包含特定节点的两个日志条目之间的时间差"""
        log_entries = []

        # 收集包含该节点的所有日志条目
        for pipeline, data in self.pipeline_data.items():
            if node_name in data['nodes']:
                log_entries.extend(data['nodes'][node_name]['log_entries'])

        # 如果没有找到日志条目，返回None
        if not log_entries:
            return None

        # 查找匹配模式1和模式2的日志条目
        entry1 = None
        entry2 = None

        for entry in log_entries:
            content = entry['content']

            # 检查是否匹配模式1
            if re.search(pattern1, content) and not entry1:
                entry1 = entry

            # 检查是否匹配模式2
            if re.search(pattern2, content) and not entry2:
                entry2 = entry

            # 如果两个都找到了，停止搜索
            if entry1 and entry2:
                break

        # 计算时间差（毫秒）
        if entry1 and entry2:
            return abs(entry1['timestamp'] - entry2['timestamp'])

        return None

    def save_node_info(self, output_file):
        """将节点信息保存到文本文件"""
        with open(output_file, 'w', encoding='utf-8', errors='ignore') as f:
            f.write("Camera Pipeline Analysis Report\n")
            f.write("=" * 80 + "\n\n")

            # 输出会话信息
            f.write("Sessions:\n")
            for session_id, session_data in self.sessions.items():
                duration = "ongoing"
                if session_data['end_time']:
                    duration = f"{session_data['end_time'] - session_data['start_time']} ms"

                f.write(f"  - Session {session_id} (Camera {session_data['camera_id']}): "
                        f"{session_data['start_time']} to {session_data['end_time'] or 'now'} "
                        f"({duration})\n")
                f.write(f"    Pipelines: {', '.join(session_data['pipelines'])}\n")
            f.write("\n")

            # 输出pipeline信息
            for pipeline, data in self.pipeline_data.items():
                session_id = data['session_id'] or "anonymous"
                camera_id = data['camera_id'] or "unknown"
                stream_state = data['stream_state']
                stream_duration = "N/A"

                if data['stream_on_time'] and data['stream_off_time']:
                    stream_duration = f"{data['stream_off_time'] - data['stream_on_time']} ms"

                f.write(f"Pipeline: {pipeline} (Session: {session_id}, Camera: {camera_id})\n")
                f.write(f"Stream State: {stream_state} (Duration: {stream_duration})\n")
                f.write(f"Nodes ({len(data['nodes'])}):\n")

                for node, node_data in data['nodes'].items():
                    f.write(f"\n  - Node: {node}\n")
                    f.write(f"    Type: {node_data['type']}\n")
                    f.write(f"    Input Ports: {node_data['input_ports']}\n")
                    f.write(f"    Output Ports: {node_data['output_ports_count']}\n")

                    if node_data['output_ports']:
                        f.write("    Active Output Ports:\n")
                        for port, port_info in node_data['output_ports'].items():
                            f.write(f"      Port {port}: {port_info['info']}\n")
                            for conn in port_info['connected_to']:
                                conn_type = "Sink Buffer" if conn['type'] == 'sink' else "Node"
                                conn_type = "Source Buffer" if conn['type'] == 'source' else conn_type
                                f.write(f"        -> {conn['node']} ({conn_type}, Port: {conn['port']})\n")
                    else:
                        f.write("    No active output ports\n")

                    # 记录日志条目数量
                    log_count = len(node_data['log_entries'])
                    f.write(f"    Log References: {log_count} entries\n")

                f.write("\n" + "-" * 70 + "\n\n")

            # 输出pipeline连接关系
            f.write("Pipeline Connections:\n")
            for pipeline, connected_pipelines in self.camera_connections.items():
                if connected_pipelines:
                    f.write(f"  - {pipeline} connected to: {', '.join(connected_pipelines)}\n")
                else:
                    f.write(f"  - {pipeline} has no external connections\n")

            f.write("\n" + "=" * 80 + "\n")
            f.write(f"Total Pipelines: {len(self.pipeline_data)}\n")
            f.write(f"Analysis completed at: {datetime.datetime.now()}\n")

    def generate_graphviz(self, output_dir, format='png'):
        """直接生成Graphviz图表并保存为PNG文件"""
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # 节点颜色映射
        node_colors = {
            'Sensor': 'lightcoral',
            'IFE': 'lightgreen',
            'Stats': 'lightyellow',
            'AWB': 'plum',
            'StatsParse': 'lightsteelblue',
            'SinkBuffer': 'gold',
            'SourceBuffer': 'orange'
        }

        # 连接颜色映射
        conn_colors = {
            'node': 'black',
            'sink': 'red',
            'source': 'blue'
        }

        # 为每个会话生成一个图
        png_files = []
        for session_id, session_data in self.sessions.items():
            pipelines = session_data['pipelines']
            if not pipelines:
                continue

            # 创建Graphviz图
            dot = Digraph(
                name=f'session_{session_id}', # 图片 名
                comment=f'Session: {session_id}',
                format=format,
                graph_attr={
                    'label': f'Session {session_id} (Camera {session_data["camera_id"]})', #Session 名
                    'labelloc': 't',
                    'fontname': 'Helvetica',
                    'fontsize': '16',
                    'rankdir': 'LR'
                },
                node_attr={
                    'shape': 'box',
                    'style': 'filled',
                    'fontname': 'Helvetica'
                },
                edge_attr={
                    'fontname': 'Helvetica',
                    'fontsize': '10'
                }
            )

            # 添加pipeline节点
            for pipeline in pipelines:
                if pipeline not in self.pipeline_data:
                    continue

                self.logger.info(f"session_id: {session_id} pipeline: {pipeline}")

                pipeline_data = self.pipeline_data[pipeline]
                state_color = 'lightgreen' if pipeline_data['stream_state'] == 'on' else 'lightgrey'
                label = f"{pipeline}\\n(Camera {pipeline_data['camera_id']})\\nStream: {pipeline_data['stream_state']}"

                # 添加pipeline节点,用于指定图形对象的形状为椭圆
                dot.node(pipeline, label=label, fillcolor=state_color, shape='ellipse')

                self.logger.info(f"label: {label} state_color: {state_color}")

                # 添加pipeline内的节点
                with dot.subgraph(name=f'cluster_{pipeline}') as subgraph:
                    subgraph.attr(label=f'Pipeline: {pipeline}', style='filled', fillcolor='lightyellow', color='lightgrey')

                    for node, node_data in self.pipeline_data[pipeline]['nodes'].items():
                        node_type = node_data['type']
                        color = node_colors.get(node_type, 'lightblue')

                        # 创建节点标签
                        node_label = f"{node}\\n({node_type})"
                        if node_data['output_ports']:
                            #ports = ", ".join([f"Port {p}" for p in node_data['output_ports'].keys()])
                            #node_label += f"\\nOutputs: {ports}"
                            ports = ", ".join([f" {p}" for p in node_data['output_ports'].keys()])
                            node_label += f"\\nOutputs Port: {ports}"

                        self.logger.info(f"node_label: {node_label}")
                        subgraph.node(node, label=node_label, fillcolor=color)

            # 添加pipeline内的连接
            for pipeline in pipelines:
                if pipeline not in self.pipeline_data:
                    continue

                for node, node_data in self.pipeline_data[pipeline]['nodes'].items():
                    for port, port_info in node_data['output_ports'].items():
                        for conn in port_info['connected_to']:
                            # 检查目标节点是否在同一pipeline中, 条件表达式，检查当前遍历到的 conn['node'] 是否存在于 self.pipeline_data[pipeline]['nodes'] 集合中
                            target_in_same_pipeline = any(
                                conn['node'] in self.pipeline_data[pipeline]['nodes']
                                for pipeline in pipelines
                            )

                            if target_in_same_pipeline: #属于同一个 pipeline, 才可以连接
                                # 设置连接属性
                                edge_attrs = {
                                    'label': f"Port {port}",
                                    'color': conn_colors[conn['type']]
                                }

                                # 对于特殊连接，使用虚线
                                if conn['type'] in ('sink', 'source'):
                                    edge_attrs['style'] = 'dashed'

                                dot.edge(
                                    node,
                                    conn['node'],
                                    **edge_attrs
                                )

            # 添加pipeline之间的连接
            for source_pipeline in pipelines:
                for target_pipeline in self.camera_connections[source_pipeline]:
                    if target_pipeline in pipelines:
                        dot.edge(
                            source_pipeline,
                            target_pipeline,
                            label="Buffer Connect",
                            color='purple',
                            style='dashed',
                            constraint='false'  # 避免影响内部布局
                        )

            # 保存PNG文件
            output_path = os.path.join(output_dir, f"session_{session_id}.{format}")
            #由于`render`方法需要指定文件名（不含扩展名），因此我们传递文件名前缀即可，它会自动添加.gv（用于DOT）和.png（用于输出）扩展名。
            # 但我们不希望保留DOT文件，所以设置`cleanup=True`。
            dot.render(filename=output_path, cleanup=True)
            png_files.append(output_path)
            self.logger.info(f"Generated PNG file: {output_path}")

        # 为匿名pipeline生成单独的图
        anonymous_pipelines = [
            p for p, data in self.pipeline_data.items()
            if not data.get('session_id')
        ]

        if anonymous_pipelines:
            dot = Digraph(
                name='anonymous_pipelines',
                comment='Anonymous Pipelines',
                format=format,
                graph_attr={
                    'label': 'Anonymous Pipelines (No Session)',
                    'labelloc': 't',
                    'fontname': 'Helvetica',
                    'fontsize': '16',
                    'rankdir': 'LR'
                },
                node_attr={
                    'shape': 'box',
                    'style': 'filled',
                    'fontname': 'Helvetica'
                },
                edge_attr={
                    'fontname': 'Helvetica',
                    'fontsize': '10'
                }
            )

            for pipeline in anonymous_pipelines:
                pipeline_data = self.pipeline_data[pipeline]
                state_color = 'lightgreen' if pipeline_data['stream_state'] == 'on' else 'lightgrey'
                label = f"{pipeline}\\nStream: {pipeline_data['stream_state']}"
                dot.node(pipeline, label=label, fillcolor=state_color, shape='ellipse')

                with dot.subgraph(name=f'cluster_{pipeline}') as subgraph:
                    subgraph.attr(label=f'Pipeline: {pipeline}', style='filled', fillcolor='lightyellow', color='lightgrey')

                    for node, node_data in self.pipeline_data[pipeline]['nodes'].items():
                        node_type = node_data['type']
                        color = node_colors.get(node_type, 'lightblue')

                        # 创建节点标签
                        node_label = f"{node}\\n({node_type})"
                        if node_data['output_ports']:
                            ports = ", ".join([f"Port {p}" for p in node_data['output_ports'].keys()])
                            node_label += f"\\nOutputs: {ports}"

                        subgraph.node(node, label=node_label, fillcolor=color)

            # 保存匿名pipeline的PNG文件
            output_path = os.path.join(output_dir, f"anonymous_pipelines.{format}")
            dot.render(filename=output_path, cleanup=True)
            png_files.append(output_path)
            self.logger.info(f"Generated PNG file for anonymous pipelines: {output_path}")

        return png_files

    def find_pipeline_connections(self, target_pipeline):
        """查找与目标pipeline有连接的所有pipeline"""
        if target_pipeline in self.camera_connections:
            return list(self.camera_connections[target_pipeline])
        return []

    def get_stream_state(self, pipeline):
        """获取pipeline的stream状态"""
        if pipeline in self.pipeline_data:
            return self.pipeline_data[pipeline]['stream_state']
        return None

    def get_session_pipelines(self, session_id):
        """获取会话中的所有pipeline"""
        if session_id in self.sessions:
            return list(self.sessions[session_id]['pipelines'])
        return []

    def profile_camera(self):
        # 保存节点信息到文本文件
        self.save_node_info("pipeline_report.txt")
        self.logger.info("Saved pipeline report to pipeline_report.txt")

        # 直接生成PNG图像
        png_dir = "pipeline_visualization"
        if not os.path.exists(png_dir):
            os.makedirs(png_dir)

        png_files = self.generate_graphviz(png_dir)
        log = (f"Generated {len(png_files)} PNG files in {png_dir}")

        # 示例：查询特定端口信息
        port_info = self.get_port_info(
            "ZSLSnapshotFormatConvertor3_0_cam_4",
            "SourceBufferFormat0",
            "0"
        )

        if port_info:
            log += ("\nSourceBufferFormat0 Port 0 Info:")
            log += (f"\nFull Path: {port_info['full_path']}")
            log += (f"\nNode Type: {port_info['node_type']}")
            log += (f"\nTotal Ports: {port_info['total_ports']}")
            log += (f"\nInfo: {port_info['info']}")
            log += ("\nConnections:")
            for conn in port_info['connected_to']:
                conn_type = "Sink Buffer" if conn['type'] == 'sink' else "Node"
                conn_type = "Source Buffer" if conn['type'] == 'source' else conn_type
                log +=(f"\n  -> {conn['node']} ({conn_type}, Port: {conn['port']})")

        # 示例：查找pipeline连接
        log +=("\nFinding connections for FastAECRealtime_0_cam_0:")
        connections = self.find_pipeline_connections("FastAECRealtime_0_cam_0")
        for conn in connections:
            log += (f"\nConnected to: {conn}")

        # 示例：获取stream状态
        log += ("\nStream states:")
        for pipeline in self.pipeline_data:
            state = self.get_stream_state(pipeline)
            log += (f"\n  - {pipeline}: {state}")

        self.logger.info(log)

# 使用示例
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

    # 初始化日志分析器
    analyzer = CameraLogAnalyzer(log_file)
    analyzer.profile_camera()