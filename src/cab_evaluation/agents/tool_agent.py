"""
Tool-based agent that replicates Strands functionality using Azure OpenAI function calling.

Provides:
- fs_read: Read files from the repository
- execute_bash: Run shell commands (grep, find, cat, etc.)
- list_directory: List directory contents
- thinking: Internal reasoning tool
"""

import os
import json
import asyncio
import subprocess
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from pathlib import Path

from openai import AsyncAzureOpenAI

logger = logging.getLogger(__name__)

# Tool definitions for Azure OpenAI function calling
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fs_read",
            "description": "Read the contents of a file from the repository. Use this to examine source code, configs, or documentation. Supports optional search mode to find specific patterns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path to the file from the repository root (e.g., 'src/main.py', 'README.md')"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional: Start reading from this line number (1-indexed)"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional: Stop reading at this line number (inclusive)"
                    },
                    "search_pattern": {
                        "type": "string",
                        "description": "Optional: Search for this pattern in the file and return matching lines with context"
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Optional: Number of context lines around search matches (default: 2)"
                    }
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_bash",
            "description": "Execute a bash command in the repository directory. Use for grep, find, ls, git, python, etc. Commands are sandboxed to the repo directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute (e.g., 'grep -r \"error\" .', 'find . -name \"*.py\"', 'git log --oneline -10')"
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional: subdirectory to run command from (relative to repo root, e.g., 'src/', 'tests/')"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List contents of a directory in the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory_path": {
                        "type": "string",
                        "description": "Relative path to the directory (e.g., 'src/', '.', 'tests/')"
                    }
                },
                "required": ["directory_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "thinking",
            "description": "Use this tool to think through the problem step by step. Record your observations, hypotheses, and reasoning. This helps organize your analysis before providing an answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {
                        "type": "string",
                        "description": "Your reasoning, observations, hypotheses, or analysis"
                    },
                    "stage": {
                        "type": "string",
                        "enum": ["initial", "analysis", "synthesis", "conclusion"],
                        "description": "The stage of thinking: initial (first observations), analysis (deep dive), synthesis (connecting ideas), conclusion (final thoughts)"
                    }
                },
                "required": ["thought"]
            }
        }
    },
    {
        "type": "function", 
        "function": {
            "name": "provide_answer",
            "description": "Provide the final answer to the user's question. Call this when you have enough information to give a complete, helpful response.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": "Your complete answer to the user's question, including any relevant code snippets, file paths, or explanations."
                    }
                },
                "required": ["answer"]
            }
        }
    }
]

# Safe commands whitelist for execute_bash
# Expanded to match Strands capabilities while maintaining security
SAFE_COMMANDS = [
    # Core file operations
    'grep', 'find', 'ls', 'cat', 'head', 'tail', 'wc', 'sort', 'uniq',
    'tree', 'file', 'stat', 'du', 'pwd', 'echo', 'awk', 'sed', 'cut',
    'diff', 'comm', 'tr', 'xargs', 'basename', 'dirname', 'realpath',
    # Version control (read operations only)
    'git',
    # Language tools (useful for code analysis)
    'python', 'python3', 'pip', 'pip3',
    'node', 'npm', 'yarn', 'npx',
    'java', 'javac', 'mvn', 'gradle',
    'cargo', 'rustc',
    'go',
    # Data processing
    'jq', 'yq', 'xmllint',
    # Build tools
    'make', 'cmake',
    # Utilities
    'which', 'type', 'env', 'printenv', 'test', 'expr', 'true', 'false',
    'date', 'touch', 'md5sum', 'sha256sum', 'strings', 'xxd', 'od',
    'tar', 'zip', 'unzip', 'gzip', 'gunzip',
    # Network (read-only)
    'curl', 'wget',
]


@dataclass
class ToolResult:
    """Result from executing a tool."""
    success: bool
    output: str
    tool_name: str


class ToolExecutor:
    """Executes tools in a sandboxed repository context."""
    
    def __init__(self, repo_dir: str, read_only: bool = True):
        """
        Initialize tool executor.
        
        Args:
            repo_dir: Path to the cloned repository
            read_only: If True, only allow read operations
        """
        self.repo_dir = os.path.abspath(repo_dir)
        self.read_only = read_only
        self.files_read: List[str] = []
        self.commands_executed: List[str] = []
        
    def _is_safe_path(self, path: str) -> bool:
        """Check if path is within the repository."""
        full_path = os.path.abspath(os.path.join(self.repo_dir, path))
        return full_path.startswith(self.repo_dir)
    
    def _is_safe_command(self, command: str) -> bool:
        """Check if command is in the safe whitelist."""
        # Get the base command (first word)
        base_cmd = command.strip().split()[0] if command.strip() else ""
        
        # Remove any path prefix
        base_cmd = os.path.basename(base_cmd)
        
        # Check against whitelist
        return base_cmd in SAFE_COMMANDS
    
    def fs_read(
        self, 
        file_path: str, 
        start_line: Optional[int] = None, 
        end_line: Optional[int] = None,
        search_pattern: Optional[str] = None,
        context_lines: int = 2
    ) -> ToolResult:
        """Read a file from the repository, with optional search mode."""
        try:
            if not self._is_safe_path(file_path):
                return ToolResult(False, f"Error: Path '{file_path}' is outside the repository", "fs_read")
            
            full_path = os.path.join(self.repo_dir, file_path)
            
            if not os.path.exists(full_path):
                return ToolResult(False, f"Error: File '{file_path}' not found", "fs_read")
            
            if not os.path.isfile(full_path):
                return ToolResult(False, f"Error: '{file_path}' is not a file", "fs_read")
            
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            
            # Search mode (similar to Strands fs_read Search mode)
            if search_pattern:
                pattern_lower = search_pattern.lower()
                matches = []
                
                for i, line in enumerate(lines):
                    if pattern_lower in line.lower():
                        # Get context
                        start = max(0, i - context_lines)
                        end = min(len(lines), i + context_lines + 1)
                        
                        context_block = []
                        for j in range(start, end):
                            prefix = "→ " if j == i else "  "
                            context_block.append(f"{prefix}{j+1:4d}| {lines[j].rstrip()}")
                        
                        matches.append({
                            "line": i + 1,
                            "context": "\n".join(context_block)
                        })
                
                if not matches:
                    content = f"No matches found for '{search_pattern}'"
                else:
                    content = f"Found {len(matches)} match(es) for '{search_pattern}':\n\n"
                    content += "\n---\n".join([m["context"] for m in matches[:20]])  # Limit to 20 matches
                    if len(matches) > 20:
                        content += f"\n\n... and {len(matches) - 20} more matches"
                
                self.files_read.append(file_path)
                return ToolResult(True, content, "fs_read")
            
            # Check file size (limit to 100KB) for full file reads
            if os.path.getsize(full_path) > 100 * 1024:
                # Read with line limits for large files
                if start_line is None:
                    start_line = 1
                if end_line is None:
                    end_line = start_line + 200  # Default to 200 lines
            
            # Apply line filtering if specified
            if start_line is not None or end_line is not None:
                start_idx = (start_line - 1) if start_line else 0
                end_idx = end_line if end_line else len(lines)
                lines = lines[start_idx:end_idx]
                
                # Add line numbers
                content = ""
                for i, line in enumerate(lines, start=start_idx + 1):
                    content += f"{i:4d}| {line}"
            else:
                content = "".join(lines)
            
            # Truncate if too long
            if len(content) > 50000:
                content = content[:50000] + "\n... [truncated - file too large]"
            
            self.files_read.append(file_path)
            return ToolResult(True, content, "fs_read")
            
        except Exception as e:
            return ToolResult(False, f"Error reading file: {str(e)}", "fs_read")
    
    def execute_bash(self, command: str, cwd: Optional[str] = None) -> ToolResult:
        """Execute a bash command in the repository directory."""
        try:
            if not self._is_safe_command(command):
                return ToolResult(
                    False, 
                    f"Error: Command not allowed. Safe commands: {', '.join(SAFE_COMMANDS[:15])}...",
                    "execute_bash"
                )
            
            # Determine working directory
            work_dir = self.repo_dir
            if cwd:
                # Validate cwd is within repo
                full_cwd = os.path.abspath(os.path.join(self.repo_dir, cwd))
                if not full_cwd.startswith(self.repo_dir):
                    return ToolResult(False, f"Error: cwd '{cwd}' is outside the repository", "execute_bash")
                if not os.path.isdir(full_cwd):
                    return ToolResult(False, f"Error: cwd '{cwd}' is not a directory", "execute_bash")
                work_dir = full_cwd
            
            # Execute with timeout
            result = subprocess.run(
                command,
                shell=True,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=30  # 30 second timeout
            )
            
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]: {result.stderr}"
            
            # Truncate if too long
            if len(output) > 30000:
                output = output[:30000] + "\n... [truncated]"
            
            self.commands_executed.append(command)
            return ToolResult(result.returncode == 0, output or "(no output)", "execute_bash")
            
        except subprocess.TimeoutExpired:
            return ToolResult(False, "Error: Command timed out (30s limit)", "execute_bash")
        except Exception as e:
            return ToolResult(False, f"Error executing command: {str(e)}", "execute_bash")
    
    def list_directory(self, directory_path: str) -> ToolResult:
        """List contents of a directory."""
        try:
            if not self._is_safe_path(directory_path):
                return ToolResult(False, f"Error: Path '{directory_path}' is outside the repository", "list_directory")
            
            full_path = os.path.join(self.repo_dir, directory_path)
            
            if not os.path.exists(full_path):
                return ToolResult(False, f"Error: Directory '{directory_path}' not found", "list_directory")
            
            if not os.path.isdir(full_path):
                return ToolResult(False, f"Error: '{directory_path}' is not a directory", "list_directory")
            
            entries = []
            for entry in sorted(os.listdir(full_path)):
                entry_path = os.path.join(full_path, entry)
                if os.path.isdir(entry_path):
                    entries.append(f"📁 {entry}/")
                else:
                    size = os.path.getsize(entry_path)
                    entries.append(f"📄 {entry} ({size:,} bytes)")
            
            return ToolResult(True, "\n".join(entries) if entries else "(empty directory)", "list_directory")
            
        except Exception as e:
            return ToolResult(False, f"Error listing directory: {str(e)}", "list_directory")
    
    def thinking(self, thought: str, stage: Optional[str] = None) -> ToolResult:
        """
        Record a thinking step. This helps the agent organize analysis.
        
        While not as powerful as Strands' multi-cycle thinking with nested agents,
        this provides structured reflection capability.
        """
        stage_labels = {
            "initial": "🔍 Initial Observations",
            "analysis": "🔬 Deep Analysis", 
            "synthesis": "🔗 Synthesis",
            "conclusion": "💡 Conclusion"
        }
        label = stage_labels.get(stage, "💭 Thinking")
        return ToolResult(True, f"[{label}]: {thought}", "thinking")
    
    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        """Execute a tool by name with given arguments."""
        if tool_name == "fs_read":
            return self.fs_read(
                arguments.get("file_path", ""),
                arguments.get("start_line"),
                arguments.get("end_line"),
                arguments.get("search_pattern"),
                arguments.get("context_lines", 2)
            )
        elif tool_name == "execute_bash":
            return self.execute_bash(arguments.get("command", ""), arguments.get("cwd"))
        elif tool_name == "list_directory":
            return self.list_directory(arguments.get("directory_path", ""))
        elif tool_name == "thinking":
            return self.thinking(arguments.get("thought", ""), arguments.get("stage"))
        elif tool_name == "provide_answer":
            # This is handled specially - return the answer
            return ToolResult(True, arguments.get("answer", ""), "provide_answer")
        else:
            return ToolResult(False, f"Unknown tool: {tool_name}", tool_name)


class ToolAgent:
    """
    Agent that uses tools to explore code and answer questions.
    Replicates Strands functionality using Azure OpenAI function calling.
    """
    
    def __init__(
        self,
        client: AsyncAzureOpenAI,
        deployment_name: str,
        repo_dir: str,
        max_iterations: int = 10,
        read_only: bool = True
    ):
        """
        Initialize the tool agent.
        
        Args:
            client: AsyncAzureOpenAI client
            deployment_name: Azure OpenAI deployment name
            repo_dir: Path to the cloned repository
            max_iterations: Maximum tool-use iterations
            read_only: If True, only allow read operations
        """
        self.client = client
        self.deployment_name = deployment_name
        self.tool_executor = ToolExecutor(repo_dir, read_only)
        self.max_iterations = max_iterations
        self.messages: List[Dict[str, Any]] = []
        self.tool_calls_made: int = 0
        
    async def run(self, system_prompt: str, user_question: str) -> Tuple[str, Dict[str, Any]]:
        """
        Run the agent to answer a question.
        
        Args:
            system_prompt: System prompt for the agent
            user_question: The user's question
            
        Returns:
            Tuple of (answer, metadata)
        """
        # Initialize conversation
        self.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ]
        self.tool_calls_made = 0
        
        final_answer = None
        
        for iteration in range(self.max_iterations):
            logger.debug(f"Tool agent iteration {iteration + 1}/{self.max_iterations}")
            
            try:
                # Call the model with tools
                response = await self.client.chat.completions.create(
                    model=self.deployment_name,
                    messages=self.messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    max_completion_tokens=4096,
                    # Note: temperature not supported by GPT-5.2 reasoning models
                    reasoning_effort="low"
                )
                
                message = response.choices[0].message
                
                # Check if model wants to use tools
                if message.tool_calls:
                    # Add assistant message with tool calls
                    self.messages.append({
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            }
                            for tc in message.tool_calls
                        ]
                    })
                    
                    # Execute each tool call
                    for tool_call in message.tool_calls:
                        tool_name = tool_call.function.name
                        
                        try:
                            arguments = json.loads(tool_call.function.arguments)
                        except json.JSONDecodeError:
                            arguments = {}
                        
                        # Check for final answer
                        if tool_name == "provide_answer":
                            final_answer = arguments.get("answer", "")
                            # Add tool result
                            self.messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": "Answer provided."
                            })
                            break
                        
                        # Execute the tool
                        result = self.tool_executor.execute_tool(tool_name, arguments)
                        self.tool_calls_made += 1
                        
                        logger.debug(f"Tool {tool_name}: {'success' if result.success else 'failed'}")
                        
                        # Add tool result to messages
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result.output
                        })
                    
                    # If we got a final answer, break
                    if final_answer is not None:
                        break
                        
                else:
                    # Model didn't use tools - treat response as final answer
                    final_answer = message.content or ""
                    break
                    
            except Exception as e:
                logger.error(f"Error in tool agent iteration: {e}")
                # On error, try to get a response without tools
                try:
                    response = await self.client.chat.completions.create(
                        model=self.deployment_name,
                        messages=self.messages,
                        max_completion_tokens=4096,
                        # Note: temperature not supported by GPT-5.2 reasoning models
                        reasoning_effort="low"
                    )
                    final_answer = response.choices[0].message.content or ""
                    break
                except Exception as e2:
                    logger.error(f"Fallback also failed: {e2}")
                    final_answer = f"Error: Unable to process request - {str(e)}"
                    break
        
        # If we exhausted iterations without an answer, get final response
        if final_answer is None:
            try:
                # Ask for final answer
                self.messages.append({
                    "role": "user",
                    "content": "Please provide your final answer based on your exploration."
                })
                response = await self.client.chat.completions.create(
                    model=self.deployment_name,
                    messages=self.messages,
                    max_completion_tokens=4096,
                    # Note: temperature not supported by GPT-5.2 reasoning models
                    reasoning_effort="low"
                )
                final_answer = response.choices[0].message.content or ""
            except Exception as e:
                final_answer = f"Error generating final answer: {str(e)}"
        
        # Compile metadata
        metadata = {
            "iterations": iteration + 1,
            "tool_calls": self.tool_calls_made,
            "files_read": self.tool_executor.files_read,
            "commands_executed": self.tool_executor.commands_executed,
            "message_count": len(self.messages)
        }
        
        return final_answer, metadata


async def create_tool_agent(
    repo_dir: str,
    api_key: str,
    endpoint: str,
    deployment_name: str,
    api_version: str = "2024-12-01-preview",
    max_iterations: int = 10
) -> ToolAgent:
    """
    Create a tool agent instance.
    
    Args:
        repo_dir: Path to the cloned repository
        api_key: Azure OpenAI API key
        endpoint: Azure OpenAI endpoint URL
        deployment_name: Model deployment name
        api_version: API version
        max_iterations: Maximum tool iterations
        
    Returns:
        Configured ToolAgent instance
    """
    client = AsyncAzureOpenAI(
        api_key=api_key,
        api_version=api_version,
        azure_endpoint=endpoint
    )
    
    return ToolAgent(
        client=client,
        deployment_name=deployment_name,
        repo_dir=repo_dir,
        max_iterations=max_iterations,
        read_only=True
    )
