#!/usr/bin/env python3
"""Generate an interactive HTML viewer from CAB evaluation results.

Usage:
    python scripts/generate_viewer.py results/gen_python_strands_v2.jsonl
    
This will create results/viewer.html with a fully functional interactive viewer.
"""

import json
import html
import argparse
from pathlib import Path
from typing import List, Dict, Any


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    if not text:
        return ""
    return html.escape(str(text))


def format_message_content(content: str) -> str:
    """Format message content with code blocks and paragraphs."""
    if not content:
        return ""
    
    # Escape HTML first
    content = escape_html(content)
    
    # Convert markdown code blocks to HTML
    lines = content.split('\n')
    result = []
    in_code_block = False
    code_lines = []
    
    for line in lines:
        if line.strip().startswith('```'):
            if in_code_block:
                # End code block
                result.append('<pre><code>' + '\n'.join(code_lines) + '</code></pre>')
                code_lines = []
                in_code_block = False
            else:
                # Start code block
                in_code_block = True
        elif in_code_block:
            code_lines.append(line)
        else:
            # Regular line - wrap in paragraph if not empty
            if line.strip():
                # Convert inline code
                import re
                line = re.sub(r'`([^`]+)`', r'<code>\1</code>', line)
                result.append(f'<p>{line}</p>')
    
    # Handle unclosed code block
    if code_lines:
        result.append('<pre><code>' + '\n'.join(code_lines) + '</code></pre>')
    
    return '\n'.join(result)


def generate_html(results: List[Dict[str, Any]], output_path: str):
    """Generate HTML viewer from results."""
    
    # Calculate stats
    satisfied_count = sum(1 for r in results if r.get('satisfaction_status') == 'FULLY_SATISFIED')
    partial_count = sum(1 for r in results if r.get('satisfaction_status') == 'PARTIALLY_SATISFIED')
    not_satisfied_count = sum(1 for r in results if r.get('satisfaction_status') == 'NOT_SATISFIED')
    error_count = sum(1 for r in results if r.get('satisfaction_status') in ('ERROR', 'AGENT_ERROR'))
    total_count = len(results)
    
    # Generate issue cards HTML
    issue_cards_html = []
    for i, result in enumerate(results):
        status = result.get('satisfaction_status', 'UNKNOWN')
        if status == 'FULLY_SATISFIED':
            status_class = 'satisfied'
            status_label = 'Satisfied'
        elif status == 'PARTIALLY_SATISFIED':
            status_class = 'partial'
            status_label = 'Partial'
        elif status == 'NOT_SATISFIED':
            status_class = 'not-satisfied'
            status_label = 'Not Satisfied'
        else:
            status_class = 'error'
            status_label = 'Error'
        
        repo = result.get('repository', 'unknown')
        issue_id = result.get('issue_id', i)
        title = escape_html(result.get('question_title', 'Untitled')[:60])
        language = result.get('language', 'Unknown')
        turns = result.get('total_conversation_rounds', 0)
        
        active_class = 'active' if i == 0 else ''
        
        issue_cards_html.append(f'''
            <div class="issue-card {status_class} {active_class}" data-index="{i}" data-status="{status_class}">
                <div class="issue-header">
                    <span class="issue-id">{repo} #{issue_id}</span>
                    <span class="issue-status {status_class}">{status_label}</span>
                </div>
                <div class="issue-title">{title}</div>
                <div class="issue-meta">
                    <span>{language}</span>
                    <span>{turns} turns</span>
                </div>
            </div>
        ''')
    
    # Generate issue details JSON for JavaScript
    issues_data = []
    for result in results:
        conversation = result.get('conversation_history', [])
        
        # Group conversation into turns (user + maintainer pairs)
        turns = []
        current_turn = []
        for msg in conversation:
            role = msg.get('role', '')
            content = msg.get('content', '')
            if role in ('user', 'maintainer'):
                current_turn.append({
                    'role': role,
                    'content': format_message_content(content)
                })
                if role == 'maintainer' or (role == 'user' and len(current_turn) > 1):
                    if current_turn:
                        turns.append(current_turn)
                        current_turn = []
        if current_turn:
            turns.append(current_turn)
        
        issues_data.append({
            'title': result.get('question_title', 'Untitled'),
            'repo': result.get('repository', 'unknown'),
            'issue_id': result.get('issue_id', 0),
            'language': result.get('language', 'Unknown'),
            'turns_count': result.get('total_conversation_rounds', 0),
            'satisfaction_status': result.get('satisfaction_status', 'UNKNOWN'),
            'satisfaction_reason': result.get('satisfaction_reason', ''),
            'turns': turns
        })
    
    issues_json = json.dumps(issues_data, ensure_ascii=False)
    
    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CAB Results Viewer</title>
    <style>
        :root {{
            --bg-primary: #191919;
            --bg-secondary: #232323;
            --bg-tertiary: #2a2a2a;
            --bg-hover: #333333;
            --text-primary: #ececec;
            --text-secondary: #a3a3a3;
            --text-muted: #6b6b6b;
            --accent: #d97757;
            --success: #6bcf8e;
            --error: #e57373;
            --warning: #e5c07b;
            --border: #363636;
            --border-light: #2e2e2e;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            font-size: 14px;
        }}
        .container {{ display: flex; height: 100vh; }}
        
        /* Sidebar */
        .sidebar {{
            width: 360px;
            background: var(--bg-secondary);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            height: 100vh;
            position: fixed;
        }}
        .sidebar-header {{
            padding: 24px;
            border-bottom: 1px solid var(--border);
        }}
        .sidebar-header h1 {{
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 20px;
        }}
        .stats {{
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }}
        .stat {{ text-align: left; }}
        .stat-value {{
            font-size: 24px;
            font-weight: 600;
        }}
        .stat-value.success {{ color: var(--success); }}
        .stat-value.partial {{ color: var(--warning); }}
        .stat-value.error {{ color: var(--error); }}
        .stat-label {{
            font-size: 12px;
            color: var(--text-muted);
        }}
        .search-input {{
            width: 100%;
            padding: 10px 14px;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--text-primary);
            font-size: 13px;
        }}
        .search-input:focus {{
            outline: none;
            border-color: var(--accent);
        }}
        .filters {{
            padding: 16px 24px;
            border-bottom: 1px solid var(--border);
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .filter-btn {{
            padding: 6px 12px;
            background: transparent;
            border: 1px solid var(--border);
            border-radius: 6px;
            color: var(--text-secondary);
            font-size: 12px;
            cursor: pointer;
            transition: all 0.15s ease;
        }}
        .filter-btn:hover {{
            border-color: var(--text-muted);
            color: var(--text-primary);
        }}
        .filter-btn.active {{
            background: var(--accent);
            border-color: var(--accent);
            color: white;
        }}
        .issue-list {{
            flex: 1;
            overflow-y: auto;
            padding: 12px;
        }}
        .issue-card {{
            padding: 16px;
            border-radius: 8px;
            margin-bottom: 8px;
            cursor: pointer;
            transition: background 0.15s ease;
            border-left: 3px solid transparent;
        }}
        .issue-card:hover {{ background: var(--bg-tertiary); }}
        .issue-card.active {{
            background: var(--bg-tertiary);
            border-left-color: var(--accent);
        }}
        .issue-card.satisfied {{ border-left-color: var(--success); }}
        .issue-card.partial {{ border-left-color: var(--warning); }}
        .issue-card.not-satisfied {{ border-left-color: var(--error); }}
        .issue-card.error {{ border-left-color: var(--error); }}
        .issue-card.hidden {{ display: none; }}
        .issue-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }}
        .issue-id {{
            font-size: 11px;
            color: var(--text-muted);
            font-weight: 500;
        }}
        .issue-status {{
            font-size: 10px;
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 500;
        }}
        .issue-status.satisfied {{
            background: rgba(107, 207, 142, 0.15);
            color: var(--success);
        }}
        .issue-status.partial {{
            background: rgba(229, 192, 123, 0.15);
            color: var(--warning);
        }}
        .issue-status.not-satisfied, .issue-status.error {{
            background: rgba(229, 115, 115, 0.15);
            color: var(--error);
        }}
        .issue-title {{
            font-size: 13px;
            font-weight: 500;
            margin-bottom: 8px;
            line-height: 1.4;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}
        .issue-meta {{
            display: flex;
            gap: 16px;
            font-size: 11px;
            color: var(--text-muted);
        }}
        
        /* Main Content */
        .main-content {{
            flex: 1;
            margin-left: 360px;
            overflow-y: auto;
            padding: 32px 48px;
        }}
        .main-content-inner {{
            max-width: 1000px;
        }}
        .issue-detail-header {{ margin-bottom: 32px; }}
        .issue-detail-header h2 {{
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 8px;
        }}
        .issue-detail-meta {{
            font-size: 13px;
            color: var(--text-secondary);
        }}
        
        /* Verdict Banner */
        .verdict-banner {{
            padding: 20px 24px;
            border-radius: 12px;
            margin-bottom: 32px;
            display: flex;
            align-items: center;
            gap: 16px;
        }}
        .verdict-banner.satisfied {{
            background: rgba(107, 207, 142, 0.08);
            border: 1px solid rgba(107, 207, 142, 0.2);
        }}
        .verdict-banner.partial {{
            background: rgba(229, 192, 123, 0.08);
            border: 1px solid rgba(229, 192, 123, 0.2);
        }}
        .verdict-banner.not-satisfied, .verdict-banner.error {{
            background: rgba(229, 115, 115, 0.08);
            border: 1px solid rgba(229, 115, 115, 0.2);
        }}
        .verdict-icon {{ font-size: 24px; }}
        .verdict-text h3 {{
            font-size: 15px;
            font-weight: 600;
            margin-bottom: 4px;
        }}
        .verdict-banner.satisfied h3 {{ color: var(--success); }}
        .verdict-banner.partial h3 {{ color: var(--warning); }}
        .verdict-banner.not-satisfied h3, .verdict-banner.error h3 {{ color: var(--error); }}
        .verdict-text p {{
            font-size: 13px;
            color: var(--text-secondary);
        }}
        
        /* Conversation */
        .conversation-section {{ margin-bottom: 40px; }}
        .section-title {{
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 16px;
        }}
        .conversation-box {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
        }}
        .turn-box {{
            background: var(--bg-primary);
            border: 1px solid var(--border-light);
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 16px;
        }}
        .turn-box:last-child {{ margin-bottom: 0; }}
        .turn-header-label {{
            font-size: 11px;
            font-weight: 600;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 16px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--border-light);
        }}
        .message {{
            margin-bottom: 12px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border-light);
            border-radius: 8px;
            padding: 16px;
        }}
        .message:last-child {{ margin-bottom: 0; }}
        .message.user {{ border-left: 3px solid #4a6fa5; }}
        .message.maintainer {{ border-left: 3px solid var(--accent); }}
        .message-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 10px;
        }}
        .message-avatar {{
            width: 28px;
            height: 28px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 600;
        }}
        .message.user .message-avatar {{
            background: #4a6fa5;
            color: white;
        }}
        .message.maintainer .message-avatar {{
            background: var(--accent);
            color: white;
        }}
        .message-role {{ font-size: 13px; font-weight: 600; }}
        .message.user .message-role {{ color: var(--text-secondary); }}
        .message.maintainer .message-role {{ color: var(--accent); }}
        .message-content {{
            margin-top: 12px;
            font-size: 14px;
            line-height: 1.7;
            color: var(--text-primary);
        }}
        .message-content p {{ margin-bottom: 12px; }}
        .message-content p:last-child {{ margin-bottom: 0; }}
        pre {{
            background: var(--bg-secondary);
            padding: 16px;
            border-radius: 8px;
            overflow-x: auto;
            font-family: 'SF Mono', 'Fira Code', monospace;
            font-size: 12px;
            margin: 12px 0;
            border: 1px solid var(--border);
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        code {{
            font-family: 'SF Mono', 'Fira Code', monospace;
            background: var(--bg-tertiary);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 12px;
        }}
        pre code {{ background: none; padding: 0; }}
        
        /* Scrollbar */
        ::-webkit-scrollbar {{ width: 6px; }}
        ::-webkit-scrollbar-track {{ background: transparent; }}
        ::-webkit-scrollbar-thumb {{
            background: var(--border);
            border-radius: 3px;
        }}
        ::-webkit-scrollbar-thumb:hover {{ background: var(--text-muted); }}
    </style>
</head>
<body>
    <div class="container">
        <div class="sidebar">
            <div class="sidebar-header">
                <h1>CAB Results</h1>
                <div class="stats">
                    <div class="stat">
                        <div class="stat-value success">{satisfied_count}</div>
                        <div class="stat-label">Satisfied</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value partial">{partial_count}</div>
                        <div class="stat-label">Partial</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value error">{not_satisfied_count + error_count}</div>
                        <div class="stat-label">Not Satisfied</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{total_count}</div>
                        <div class="stat-label">Total</div>
                    </div>
                </div>
                <input type="text" class="search-input" placeholder="Search issues..." id="searchInput">
            </div>
            <div class="filters">
                <button class="filter-btn active" data-filter="all">All</button>
                <button class="filter-btn" data-filter="satisfied">Satisfied</button>
                <button class="filter-btn" data-filter="partial">Partial</button>
                <button class="filter-btn" data-filter="not-satisfied">Not Satisfied</button>
            </div>
            <div class="issue-list" id="issueList">
                {''.join(issue_cards_html)}
            </div>
        </div>
        
        <div class="main-content" id="mainContent">
            <div id="issueDetail">
                <p style="color: var(--text-muted);">Select an issue from the sidebar to view details.</p>
            </div>
        </div>
    </div>

    <script>
        const issuesData = {issues_json};
        
        function renderIssueDetail(index) {{
            const issue = issuesData[index];
            if (!issue) return;
            
            const statusClass = issue.satisfaction_status === 'FULLY_SATISFIED' ? 'satisfied' :
                               issue.satisfaction_status === 'PARTIALLY_SATISFIED' ? 'partial' :
                               'not-satisfied';
            const statusLabel = issue.satisfaction_status === 'FULLY_SATISFIED' ? 'User Satisfied' :
                               issue.satisfaction_status === 'PARTIALLY_SATISFIED' ? 'Partially Satisfied' :
                               'Not Satisfied';
            const statusIcon = issue.satisfaction_status === 'FULLY_SATISFIED' ? '✓' :
                              issue.satisfaction_status === 'PARTIALLY_SATISFIED' ? '◐' : '✗';
            
            let turnsHtml = '';
            issue.turns.forEach((turn, i) => {{
                let messagesHtml = '';
                turn.forEach(msg => {{
                    messagesHtml += `
                        <div class="message ${{msg.role}}">
                            <div class="message-header">
                                <div class="message-avatar">${{msg.role === 'user' ? 'U' : 'M'}}</div>
                                <span class="message-role">${{msg.role === 'user' ? 'User' : 'Maintainer'}}</span>
                            </div>
                            <div class="message-content">${{msg.content}}</div>
                        </div>
                    `;
                }});
                turnsHtml += `
                    <div class="turn-box">
                        <div class="turn-header-label">Turn ${{i + 1}}</div>
                        ${{messagesHtml}}
                    </div>
                `;
            }});
            
            document.getElementById('issueDetail').innerHTML = `
                <div class="issue-detail-header">
                    <h2>${{issue.title}}</h2>
                    <div class="issue-detail-meta">${{issue.repo}} #${{issue.issue_id}} · ${{issue.language}} · ${{issue.turns_count}} conversation turns</div>
                </div>
                
                <div class="verdict-banner ${{statusClass}}">
                    <span class="verdict-icon">${{statusIcon}}</span>
                    <div class="verdict-text">
                        <h3>${{statusLabel}}</h3>
                        <p>${{issue.satisfaction_reason || 'No reason provided.'}}</p>
                    </div>
                </div>
                
                <div class="conversation-section">
                    <div class="section-title">Conversation</div>
                    <div class="conversation-box">
                        ${{turnsHtml || '<p style="color: var(--text-muted);">No conversation recorded.</p>'}}
                    </div>
                </div>
            `;
        }}
        
        // Issue card click handler
        document.querySelectorAll('.issue-card').forEach(card => {{
            card.addEventListener('click', function() {{
                document.querySelectorAll('.issue-card').forEach(c => c.classList.remove('active'));
                this.classList.add('active');
                renderIssueDetail(parseInt(this.dataset.index));
            }});
        }});
        
        // Filter buttons
        document.querySelectorAll('.filter-btn').forEach(btn => {{
            btn.addEventListener('click', function() {{
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                this.classList.add('active');
                
                const filter = this.dataset.filter;
                document.querySelectorAll('.issue-card').forEach(card => {{
                    if (filter === 'all') {{
                        card.classList.remove('hidden');
                    }} else {{
                        const status = card.dataset.status;
                        const show = (filter === 'satisfied' && status === 'satisfied') ||
                                    (filter === 'partial' && status === 'partial') ||
                                    (filter === 'not-satisfied' && (status === 'not-satisfied' || status === 'error'));
                        card.classList.toggle('hidden', !show);
                    }}
                }});
            }});
        }});
        
        // Search functionality
        document.getElementById('searchInput').addEventListener('input', function() {{
            const query = this.value.toLowerCase();
            document.querySelectorAll('.issue-card').forEach(card => {{
                const title = card.querySelector('.issue-title').textContent.toLowerCase();
                const id = card.querySelector('.issue-id').textContent.toLowerCase();
                const matches = title.includes(query) || id.includes(query);
                card.classList.toggle('hidden', !matches);
            }});
        }});
        
        // Render first issue on load
        if (issuesData.length > 0) {{
            renderIssueDetail(0);
        }}
    </script>
</body>
</html>
'''
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"Generated viewer: {output_path}")
    print(f"  Total: {total_count} issues")
    print(f"  Satisfied: {satisfied_count}")
    print(f"  Partial: {partial_count}")
    print(f"  Not Satisfied: {not_satisfied_count}")
    print(f"  Errors: {error_count}")


def main():
    parser = argparse.ArgumentParser(description='Generate HTML viewer from CAB results')
    parser.add_argument('input', help='Input JSONL file with CAB results')
    parser.add_argument('-o', '--output', help='Output HTML file (default: results/viewer.html)')
    args = parser.parse_args()
    
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return 1
    
    output_path = args.output or str(input_path.parent / 'viewer.html')
    
    # Load results
    results = []
    with open(input_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    
    if not results:
        print("Error: No valid results found in input file")
        return 1
    
    generate_html(results, output_path)
    return 0


if __name__ == '__main__':
    exit(main())
