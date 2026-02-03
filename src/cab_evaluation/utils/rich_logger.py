"""
Rich logging utilities for CAB evaluation.

Provides beautiful structured logging with boxes, panels, and clear
visual separation between issues and conversation turns.
"""

import logging
import sys
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.logging import RichHandler
from rich.theme import Theme
from rich.style import Style
from rich import box

# Custom theme for CAB
CAB_THEME = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "red bold",
    "success": "green",
    "issue": "bold magenta",
    "turn": "bold yellow",
    "user": "blue",
    "maintainer": "green",
    "judge": "orange3",
    "muted": "dim white",
})

# Global console instance
console = Console(theme=CAB_THEME)

# File console for logging to file (no colors)
file_console: Optional[Console] = None


def setup_rich_logging(
    log_file: Optional[str] = None,
    level: int = logging.INFO
) -> logging.Logger:
    """
    Setup rich logging for the application.
    
    Args:
        log_file: Optional path to log file
        level: Logging level
        
    Returns:
        Configured logger
    """
    global file_console
    
    # Create rich handler for console
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    rich_handler.setLevel(level)
    
    # Configure root logger
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[rich_handler]
    )
    
    # Add file handler if specified
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, mode='a')
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)s | %(message)s'
        ))
        logging.getLogger().addHandler(file_handler)
        
        # Create file console for rich output to file
        file_console = Console(file=open(log_file, 'a'), force_terminal=False, width=120)
    
    return logging.getLogger(__name__)


class CABLogger:
    """
    Structured logger for CAB evaluation with visual formatting.
    
    Provides clear visual separation between issues, turns, and messages.
    """
    
    def __init__(self, log_file: Optional[str] = None):
        """Initialize CAB logger."""
        self.console = console
        self.log_file = log_file
        self.file_console = None
        
        if log_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            self.file_console = Console(
                file=open(log_file, 'a'), 
                force_terminal=False, 
                width=120,
                no_color=True
            )
        
        self.current_issue_id = None
        self.current_turn = 0
    
    def _write_to_file(self, text: str):
        """Write plain text to file."""
        if self.file_console:
            self.file_console.print(text)
    
    def _write_panel_to_file(self, content: str, title: str, border_char: str = "─"):
        """Write a text-based panel to file."""
        if not self.file_console:
            return
        
        width = 100
        border = border_char * width
        
        self.file_console.print(f"\n┌{border}┐")
        self.file_console.print(f"│ {title:<{width-2}} │")
        self.file_console.print(f"├{border}┤")
        for line in content.split('\n'):
            # Truncate long lines
            if len(line) > width - 4:
                line = line[:width-7] + "..."
            self.file_console.print(f"│ {line:<{width-2}} │")
        self.file_console.print(f"└{border}┘\n")
    
    def issue_start(
        self, 
        issue_id: str, 
        title: str, 
        language: str,
        repository: str,
        issue_number: int,
        total_issues: int
    ):
        """Log the start of a new issue processing."""
        self.current_issue_id = issue_id
        self.current_turn = 0
        
        # Create issue panel
        content = Text()
        content.append(f"Issue #{issue_id}", style="bold")
        content.append(f"\n{title}\n\n", style="white")
        content.append(f"Repository: ", style="muted")
        content.append(f"{repository}\n", style="cyan")
        content.append(f"Language: ", style="muted")
        content.append(f"{language}\n", style="cyan")
        content.append(f"Progress: ", style="muted")
        content.append(f"{issue_number}/{total_issues}", style="green")
        
        panel = Panel(
            content,
            title=f"[bold magenta]🚀 NEW ISSUE[/bold magenta]",
            subtitle=f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]",
            border_style="magenta",
            box=box.ROUNDED,
            padding=(1, 2),
        )
        
        self.console.print("\n")
        self.console.print(panel)
        
        # File logging
        self._write_panel_to_file(
            f"Issue #{issue_id}\n{title}\n\nRepository: {repository}\nLanguage: {language}\nProgress: {issue_number}/{total_issues}",
            f"🚀 NEW ISSUE - {datetime.now().strftime('%H:%M:%S')}"
        )
    
    def issue_complete(
        self, 
        issue_id: str, 
        success: bool, 
        satisfaction_status: str,
        total_turns: int,
        duration_seconds: float
    ):
        """Log issue completion."""
        status_style = "green" if success else "red"
        status_icon = "✅" if success else "❌"
        
        content = Text()
        content.append(f"Issue #{issue_id}\n\n", style="bold")
        content.append(f"Status: ", style="muted")
        content.append(f"{status_icon} {satisfaction_status}\n", style=status_style)
        content.append(f"Turns: ", style="muted")
        content.append(f"{total_turns}\n", style="cyan")
        content.append(f"Duration: ", style="muted")
        content.append(f"{duration_seconds:.1f}s", style="cyan")
        
        panel = Panel(
            content,
            title=f"[bold {status_style}]ISSUE COMPLETE[/bold {status_style}]",
            border_style=status_style,
            box=box.ROUNDED,
            padding=(1, 2),
        )
        
        self.console.print(panel)
        self.console.print("\n")
        
        # File logging
        self._write_panel_to_file(
            f"Issue #{issue_id}\n\nStatus: {status_icon} {satisfaction_status}\nTurns: {total_turns}\nDuration: {duration_seconds:.1f}s",
            f"ISSUE COMPLETE"
        )
    
    def turn_start(self, turn_number: int):
        """Log the start of a conversation turn."""
        self.current_turn = turn_number
        
        self.console.print(
            Panel(
                f"[bold yellow]Turn {turn_number}[/bold yellow]",
                box=box.ROUNDED,
                border_style="yellow",
                padding=(0, 2),
            )
        )
        
        # File logging
        if self.file_console:
            self.file_console.print(f"\n{'='*50}")
            self.file_console.print(f"  TURN {turn_number}")
            self.file_console.print(f"{'='*50}\n")
    
    def user_message(self, content: str, token_count: Optional[int] = None):
        """Log a user message."""
        # Truncate for display
        display_content = content[:500] + "..." if len(content) > 500 else content
        
        header = Text()
        header.append("👤 USER", style="bold blue")
        if token_count:
            header.append(f"  ({token_count} tokens)", style="dim")
        
        panel = Panel(
            display_content,
            title=header,
            border_style="blue",
            box=box.ROUNDED,
            padding=(1, 2),
        )
        
        self.console.print(panel)
        
        # File logging
        self._write_panel_to_file(
            display_content,
            f"👤 USER{f' ({token_count} tokens)' if token_count else ''}"
        )
    
    def maintainer_message(
        self, 
        content: str, 
        token_count: Optional[int] = None,
        exploration_files: Optional[list] = None
    ):
        """Log a maintainer response."""
        # Truncate for display
        display_content = content[:800] + "..." if len(content) > 800 else content
        
        header = Text()
        header.append("🤖 MAINTAINER", style="bold green")
        if token_count:
            header.append(f"  ({token_count} tokens)", style="dim")
        
        # Add exploration info if available
        if exploration_files:
            display_content += f"\n\n[dim]Explored: {', '.join(exploration_files[:5])}[/dim]"
        
        panel = Panel(
            display_content,
            title=header,
            border_style="green",
            box=box.ROUNDED,
            padding=(1, 2),
        )
        
        self.console.print(panel)
        
        # File logging
        file_content = content[:800] + "..." if len(content) > 800 else content
        if exploration_files:
            file_content += f"\n\nExplored: {', '.join(exploration_files[:5])}"
        self._write_panel_to_file(
            file_content,
            f"🤖 MAINTAINER{f' ({token_count} tokens)' if token_count else ''}"
        )
    
    def judge_verdict(
        self,
        verdict: str,
        confidence: float,
        criteria_met: int,
        criteria_total: int,
        reasoning: str
    ):
        """Log judge evaluation result."""
        is_correct = verdict.lower() in ['correct', 'satisfied', 'fully_satisfied']
        style = "green" if is_correct else "red"
        icon = "✅" if is_correct else "❌"
        
        content = Text()
        content.append(f"Verdict: ", style="muted")
        content.append(f"{icon} {verdict}\n", style=f"bold {style}")
        content.append(f"Confidence: ", style="muted")
        content.append(f"{confidence:.0%}\n", style="cyan")
        content.append(f"Criteria: ", style="muted")
        content.append(f"{criteria_met}/{criteria_total}\n\n", style="cyan")
        content.append(f"Reasoning:\n", style="muted")
        content.append(reasoning[:300] + "..." if len(reasoning) > 300 else reasoning, style="dim")
        
        panel = Panel(
            content,
            title="[bold orange3]⚖️ JUDGE EVALUATION[/bold orange3]",
            border_style="orange3",
            box=box.ROUNDED,
            padding=(1, 2),
        )
        
        self.console.print(panel)
        
        # File logging
        self._write_panel_to_file(
            f"Verdict: {icon} {verdict}\nConfidence: {confidence:.0%}\nCriteria: {criteria_met}/{criteria_total}\n\nReasoning:\n{reasoning[:300]}...",
            "⚖️ JUDGE EVALUATION"
        )
    
    def endpoint_info(self, endpoint_name: str, action: str):
        """Log endpoint routing information."""
        self.console.print(
            f"  [dim]→ Endpoint: {endpoint_name} ({action})[/dim]"
        )
        
        if self.file_console:
            self.file_console.print(f"  → Endpoint: {endpoint_name} ({action})")
    
    def error(self, message: str, issue_id: Optional[str] = None):
        """Log an error."""
        title = f"ERROR - Issue #{issue_id}" if issue_id else "ERROR"
        
        panel = Panel(
            message,
            title=f"[bold red]❌ {title}[/bold red]",
            border_style="red",
            box=box.ROUNDED,
            padding=(1, 2),
        )
        
        self.console.print(panel)
        
        self._write_panel_to_file(message, f"❌ {title}")
    
    def info(self, message: str):
        """Log an info message."""
        self.console.print(f"[cyan]ℹ️  {message}[/cyan]")
        
        if self.file_console:
            self.file_console.print(f"ℹ️  {message}")
    
    def success(self, message: str):
        """Log a success message."""
        self.console.print(f"[green]✅ {message}[/green]")
        
        if self.file_console:
            self.file_console.print(f"✅ {message}")
    
    def warning(self, message: str):
        """Log a warning message."""
        self.console.print(f"[yellow]⚠️  {message}[/yellow]")
        
        if self.file_console:
            self.file_console.print(f"⚠️  {message}")
    
    def router_stats(self, stats: Dict[str, Any]):
        """Log router statistics."""
        table = Table(
            title="[bold cyan]Endpoint Router Stats[/bold cyan]",
            box=box.ROUNDED,
            border_style="cyan",
        )
        
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        
        table.add_row("Total Requests", str(stats.get('total_requests', 0)))
        table.add_row("Successful", f"[green]{stats.get('successful_requests', 0)}[/green]")
        table.add_row("Failed", f"[red]{stats.get('failed_requests', 0)}[/red]")
        table.add_row("Failovers", str(stats.get('failovers', 0)))
        table.add_row("Success Rate", f"{stats.get('success_rate', 0):.1f}%")
        
        self.console.print(table)
        
        # File logging
        if self.file_console:
            self.file_console.print("\n--- Endpoint Router Stats ---")
            self.file_console.print(f"Total Requests: {stats.get('total_requests', 0)}")
            self.file_console.print(f"Successful: {stats.get('successful_requests', 0)}")
            self.file_console.print(f"Failed: {stats.get('failed_requests', 0)}")
            self.file_console.print(f"Failovers: {stats.get('failovers', 0)}")
            self.file_console.print(f"Success Rate: {stats.get('success_rate', 0):.1f}%")
            self.file_console.print("----------------------------\n")
    
    def summary(
        self,
        total_issues: int,
        successful: int,
        failed: int,
        total_duration: float
    ):
        """Log final summary."""
        success_rate = (successful / total_issues * 100) if total_issues > 0 else 0
        
        table = Table(
            title="[bold magenta]📊 EVALUATION SUMMARY[/bold magenta]",
            box=box.DOUBLE,
            border_style="magenta",
            padding=(1, 2),
        )
        
        table.add_column("Metric", style="cyan", justify="right")
        table.add_column("Value", style="white", justify="left")
        
        table.add_row("Total Issues", str(total_issues))
        table.add_row("Successful", f"[green]{successful}[/green]")
        table.add_row("Failed", f"[red]{failed}[/red]")
        table.add_row("Success Rate", f"[cyan]{success_rate:.1f}%[/cyan]")
        table.add_row("Total Duration", f"{total_duration:.1f}s ({total_duration/60:.1f}m)")
        table.add_row("Avg per Issue", f"{total_duration/total_issues:.1f}s" if total_issues > 0 else "N/A")
        
        self.console.print("\n")
        self.console.print(table)
        self.console.print("\n")
        
        # File logging
        if self.file_console:
            self.file_console.print("\n" + "="*60)
            self.file_console.print("  📊 EVALUATION SUMMARY")
            self.file_console.print("="*60)
            self.file_console.print(f"  Total Issues: {total_issues}")
            self.file_console.print(f"  Successful: {successful}")
            self.file_console.print(f"  Failed: {failed}")
            self.file_console.print(f"  Success Rate: {success_rate:.1f}%")
            self.file_console.print(f"  Total Duration: {total_duration:.1f}s ({total_duration/60:.1f}m)")
            self.file_console.print(f"  Avg per Issue: {total_duration/total_issues:.1f}s" if total_issues > 0 else "  Avg per Issue: N/A")
            self.file_console.print("="*60 + "\n")


# Global logger instance
_cab_logger: Optional[CABLogger] = None


def get_cab_logger(log_file: Optional[str] = None) -> CABLogger:
    """Get or create the global CAB logger."""
    global _cab_logger
    if _cab_logger is None:
        _cab_logger = CABLogger(log_file)
    return _cab_logger


def reset_cab_logger():
    """Reset the global CAB logger."""
    global _cab_logger
    _cab_logger = None
