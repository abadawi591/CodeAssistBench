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
    
    def issue_complete_with_conversation(
        self, 
        issue_id: str,
        title: str,
        success: bool, 
        satisfaction_status: str,
        total_turns: int,
        duration_seconds: float,
        conversation_history: list
    ):
        """Log issue completion with full conversation summary."""
        # Check for positive satisfaction (FULLY_SATISFIED or PARTIALLY_SATISFIED)
        is_positive = satisfaction_status.upper() in ("FULLY_SATISFIED", "PARTIALLY_SATISFIED")
        status_style = "green" if is_positive else "red"
        status_icon = "✅" if is_positive else "❌"
        
        # Build conversation summary
        from rich.table import Table
        from rich.console import Group
        
        # Header
        header = Text()
        header.append(f"Issue #{issue_id}", style="bold magenta")
        header.append(f" • {title[:60]}{'...' if len(title) > 60 else ''}\n", style="dim")
        header.append(f"Status: ", style="muted")
        header.append(f"{status_icon} {satisfaction_status}", style=status_style)
        header.append(f" • {total_turns} turns • {duration_seconds:.1f}s\n", style="dim")
        
        # Conversation turns (show last 3 turns max to keep it compact)
        conv_parts = []
        turns_to_show = conversation_history[-6:] if len(conversation_history) > 6 else conversation_history
        
        if len(conversation_history) > 6:
            conv_parts.append(Text(f"... ({len(conversation_history) - 6} earlier messages)\n", style="dim"))
        
        for msg in turns_to_show:
            role = msg.get('role', '') if isinstance(msg, dict) else getattr(msg, 'role', '')
            content = msg.get('content', '') if isinstance(msg, dict) else getattr(msg, 'content', '')
            
            # Truncate content
            display_content = content[:200] + "..." if len(content) > 200 else content
            display_content = display_content.replace('\n', ' ')[:200]
            
            msg_text = Text()
            if role == "user":
                msg_text.append("👤 User: ", style="bold blue")
            else:
                msg_text.append("🤖 Maintainer: ", style="bold green")
            msg_text.append(f"{display_content}\n", style="dim")
            conv_parts.append(msg_text)
        
        # Combine all parts
        full_content = Text()
        full_content.append_text(header)
        full_content.append("\n")
        for part in conv_parts:
            full_content.append_text(part)
        
        panel = Panel(
            full_content,
            title=f"[bold {status_style}]COMPLETED[/bold {status_style}]",
            border_style=status_style,
            box=box.ROUNDED,
            padding=(1, 2),
        )
        
        self.console.print(panel)
        
        # File logging (full conversation)
        file_content = f"Issue #{issue_id} - {title}\n"
        file_content += f"Status: {status_icon} {satisfaction_status}\n"
        file_content += f"Turns: {total_turns}, Duration: {duration_seconds:.1f}s\n\n"
        file_content += "=== Conversation ===\n"
        for msg in conversation_history:
            role = msg.get('role', '') if isinstance(msg, dict) else getattr(msg, 'role', '')
            content = msg.get('content', '') if isinstance(msg, dict) else getattr(msg, 'content', '')
            file_content += f"\n[{role.upper()}]\n{content[:500]}{'...' if len(content) > 500 else ''}\n"
        
        self._write_panel_to_file(file_content, "ISSUE COMPLETE")
    
    def issue_complete(
        self, 
        issue_id: str, 
        success: bool, 
        satisfaction_status: str,
        total_turns: int,
        duration_seconds: float
    ):
        """Log issue completion (simple version without conversation)."""
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
    
    def results_log(self, results: list):
        """Print a formatted log of all processed issues.
        
        Args:
            results: List of result dicts with keys:
                - issue_id: Issue identifier
                - satisfaction_status: FULLY_SATISFIED, PARTIALLY_SATISFIED, NOT_SATISFIED, ERROR
                - total_conversation_rounds: Number of turns
                - question_title: Issue title
                - duration_seconds: Processing time in seconds
        """
        if not results:
            return
        
        self.console.print("\n")
        self.console.print("[bold magenta]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold magenta]")
        self.console.print("[bold magenta]                              📋 RESULTS LOG                                          [/bold magenta]")
        self.console.print("[bold magenta]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold magenta]")
        
        # Sort results by issue_id for consistent display
        sorted_results = sorted(results, key=lambda x: str(x.get('issue_id', '')))
        
        for result in sorted_results:
            issue_id = str(result.get('issue_id', '?'))
            status = result.get('satisfaction_status', 'UNKNOWN')
            turns = result.get('total_conversation_rounds', 0)
            title = result.get('question_title', 'Untitled')
            duration = result.get('duration_seconds', 0)
            
            # Format status with icon
            if status == 'FULLY_SATISFIED':
                status_str = "[green]✓ SATISFIED[/green]"
                status_file = "✓ SATISFIED"
            elif status == 'PARTIALLY_SATISFIED':
                status_str = "[yellow]◐ PARTIAL[/yellow]  "
                status_file = "◐ PARTIAL"
            elif status == 'NOT_SATISFIED':
                status_str = "[red]✗ NOT_SAT[/red]  "
                status_file = "✗ NOT_SAT"
            elif status == 'AGENT_ERROR':
                status_str = "[magenta]⚠ AGENT_ERR[/magenta]"
                status_file = "⚠ AGENT_ERR"
            else:
                status_str = "[red]✗ ERROR[/red]    "
                status_file = "✗ ERROR"
            
            # Format turn number
            turn_str = f"T{turns}"
            
            # Truncate title to fit nicely (max 50 chars for main line)
            max_title_len = 50
            if len(title) > max_title_len:
                title_line1 = title[:max_title_len]
                title_line2 = title[max_title_len:max_title_len + 50] + "..." if len(title) > max_title_len + 50 else title[max_title_len:]
            else:
                title_line1 = title
                title_line2 = None
            
            # Format duration
            duration_str = f"{int(duration)}s"
            
            # Print main line
            self.console.print(
                f" [cyan]#{issue_id:<6}[/cyan] {status_str} [dim]{turn_str:<4}[/dim] {title_line1:<52} [dim]{duration_str:>8}[/dim]"
            )
            
            # Print continuation line if title was truncated
            if title_line2:
                self.console.print(f"                                {title_line2}")
        
        self.console.print("[bold magenta]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold magenta]")
        self.console.print("\n")
        
        # File logging (plain text)
        if self.file_console:
            self.file_console.print("\n" + "="*95)
            self.file_console.print("                              📋 RESULTS LOG")
            self.file_console.print("="*95)
            
            for result in sorted_results:
                issue_id = str(result.get('issue_id', '?'))
                status = result.get('satisfaction_status', 'UNKNOWN')
                turns = result.get('total_conversation_rounds', 0)
                title = result.get('question_title', 'Untitled')
                duration = result.get('duration_seconds', 0)
                
                # Plain status
                if status == 'FULLY_SATISFIED':
                    status_file = "✓ SATISFIED"
                elif status == 'PARTIALLY_SATISFIED':
                    status_file = "◐ PARTIAL"
                elif status == 'NOT_SATISFIED':
                    status_file = "✗ NOT_SAT"
                elif status == 'AGENT_ERROR':
                    status_file = "⚠ AGENT_ERR"
                else:
                    status_file = "✗ ERROR"
                
                # Truncate title
                if len(title) > 50:
                    title_display = title[:47] + "..."
                else:
                    title_display = title
                
                self.file_console.print(
                    f" #{issue_id:<6} {status_file:<12} T{turns:<3} {title_display:<52} {int(duration):>6}s"
                )
            
            self.file_console.print("="*95 + "\n")
    
    def summary(
        self,
        total_issues: int,
        successful: int,
        failed: int,
        total_duration: float,
        generation_stats: Optional[Dict[str, Any]] = None,
        evaluation_stats: Optional[Dict[str, Any]] = None,
        agreement_stats: Optional[Dict[str, Any]] = None
    ):
        """Log final summary with detailed statistics.
        
        Args:
            total_issues: Total number of issues processed
            successful: Number of successfully processed issues
            failed: Number of failed issues
            total_duration: Total processing duration in seconds
            generation_stats: Optional generation phase statistics
                - fully_satisfied, partially_satisfied, not_satisfied: Counts
                - avg_turns, avg_satisfaction_turn, min_satisfaction_turn, max_satisfaction_turn
                - max_turns_reached: Count hitting max turns
            evaluation_stats: Optional evaluation phase statistics
                - correct, partial, incorrect: Counts
                - avg_alignment: Average alignment score
            agreement_stats: Optional user-judge agreement statistics
                - matrix: 3x3 dict with counts
                - user_too_demanding, user_easily_fooled: Counts
        """
        success_rate = (successful / total_issues * 100) if total_issues > 0 else 0
        
        # Main summary table
        main_table = Table(
            title="[bold magenta]📊 PIPELINE SUMMARY[/bold magenta]",
            box=box.DOUBLE,
            border_style="magenta",
            padding=(1, 2),
        )
        
        main_table.add_column("Metric", style="cyan", justify="right")
        main_table.add_column("Value", style="white", justify="left")
        
        main_table.add_row("Total Issues", str(total_issues))
        main_table.add_row("Completed", f"[green]{successful}[/green]")
        main_table.add_row("Failed", f"[red]{failed}[/red]")
        main_table.add_row("Completion Rate", f"[cyan]{success_rate:.1f}%[/cyan]")
        main_table.add_row("Total Duration", f"{total_duration:.1f}s ({total_duration/60:.1f}m)")
        main_table.add_row("Avg per Issue", f"{total_duration/total_issues:.1f}s" if total_issues > 0 else "N/A")
        
        self.console.print("\n")
        self.console.print(main_table)
        
        # Generation stats table (if provided)
        if generation_stats:
            gen_table = Table(
                title="[bold cyan]🔄 GENERATION PHASE (User Satisfaction)[/bold cyan]",
                box=box.ROUNDED,
                border_style="cyan",
                padding=(0, 2),
            )
            
            gen_table.add_column("Metric", style="white", justify="right")
            gen_table.add_column("Count", style="white", justify="center")
            gen_table.add_column("Info", style="white", justify="left")
            
            total_gen = generation_stats.get('fully_satisfied', 0) + \
                        generation_stats.get('partially_satisfied', 0) + \
                        generation_stats.get('not_satisfied', 0)
            
            if total_gen > 0:
                fully_sat = generation_stats.get('fully_satisfied', 0)
                partial_sat = generation_stats.get('partially_satisfied', 0)
                not_sat = generation_stats.get('not_satisfied', 0)
                
                # Satisfaction status
                gen_table.add_row(
                    "✅ Fully Satisfied",
                    f"[green]{fully_sat}[/green]",
                    f"[green]{fully_sat/total_gen*100:.1f}%[/green]"
                )
                gen_table.add_row(
                    "🔶 Partially Satisfied",
                    f"[yellow]{partial_sat}[/yellow]",
                    f"[yellow]{partial_sat/total_gen*100:.1f}%[/yellow]"
                )
                gen_table.add_row(
                    "❌ Not Satisfied",
                    f"[red]{not_sat}[/red]",
                    f"[red]{not_sat/total_gen*100:.1f}%[/red]"
                )
                
                # Separator
                gen_table.add_row("[dim]───────────────────[/dim]", "[dim]─────[/dim]", "[dim]────────────────────[/dim]")
                
                # Conversation stats
                if 'avg_turns' in generation_stats:
                    gen_table.add_row(
                        "📊 Avg Turns (all)",
                        f"{generation_stats['avg_turns']:.1f}",
                        ""
                    )
                
                # Satisfaction turn stats (only if there were satisfied users)
                if generation_stats.get('avg_satisfaction_turn'):
                    gen_table.add_row(
                        "🎯 Avg Turn to Satisfaction",
                        f"[green]{generation_stats['avg_satisfaction_turn']:.1f}[/green]",
                        "[dim](when satisfied)[/dim]"
                    )
                if generation_stats.get('min_satisfaction_turn'):
                    gen_table.add_row(
                        "⚡ Earliest Satisfaction",
                        f"[green]Turn {generation_stats['min_satisfaction_turn']}[/green]",
                        ""
                    )
                if generation_stats.get('max_satisfaction_turn'):
                    gen_table.add_row(
                        "🐌 Latest Satisfaction",
                        f"[yellow]Turn {generation_stats['max_satisfaction_turn']}[/yellow]",
                        ""
                    )
                
                if 'max_turns_reached' in generation_stats:
                    gen_table.add_row(
                        "⏱️ Hit Max Turns",
                        f"[red]{generation_stats['max_turns_reached']}[/red]",
                        f"[red]{generation_stats['max_turns_reached']/total_gen*100:.1f}%[/red]"
                    )
            
            self.console.print(gen_table)
        
        # Evaluation stats table (if provided)
        if evaluation_stats:
            eval_table = Table(
                title="[bold green]⚖️ EVALUATION PHASE (Judge Verdict)[/bold green]",
                box=box.ROUNDED,
                border_style="green",
                padding=(0, 2),
            )
            
            eval_table.add_column("Verdict", style="white", justify="right")
            eval_table.add_column("Count", style="white", justify="center")
            eval_table.add_column("Percentage", style="white", justify="left")
            
            total_eval = evaluation_stats.get('correct', 0) + \
                         evaluation_stats.get('partial', 0) + \
                         evaluation_stats.get('incorrect', 0)
            
            if total_eval > 0:
                correct = evaluation_stats.get('correct', 0)
                partial = evaluation_stats.get('partial', 0)
                incorrect = evaluation_stats.get('incorrect', 0)
                
                eval_table.add_row(
                    "✅ Correct",
                    f"[green]{correct}[/green]",
                    f"[green]{correct/total_eval*100:.1f}%[/green]"
                )
                eval_table.add_row(
                    "🔶 Partial",
                    f"[yellow]{partial}[/yellow]",
                    f"[yellow]{partial/total_eval*100:.1f}%[/yellow]"
                )
                eval_table.add_row(
                    "❌ Incorrect",
                    f"[red]{incorrect}[/red]",
                    f"[red]{incorrect/total_eval*100:.1f}%[/red]"
                )
                
                if 'avg_alignment' in evaluation_stats:
                    eval_table.add_row("[dim]───────────────────[/dim]", "[dim]─────[/dim]", "[dim]────────────────────[/dim]")
                    eval_table.add_row(
                        "📈 Avg Alignment",
                        f"{evaluation_stats['avg_alignment']:.1f}%",
                        ""
                    )
            
            self.console.print(eval_table)
        
        # User-Judge Agreement stats (if provided - only after evaluation)
        if agreement_stats and agreement_stats.get('total', 0) > 0:
            agree_table = Table(
                title="[bold yellow]🤝 USER ↔ JUDGE AGREEMENT[/bold yellow]",
                box=box.ROUNDED,
                border_style="yellow",
                padding=(0, 2),
            )
            
            # Agreement matrix header
            agree_table.add_column("", style="white", justify="right")
            agree_table.add_column("Judge: ✅", style="green", justify="center")
            agree_table.add_column("Judge: 🔶", style="yellow", justify="center")
            agree_table.add_column("Judge: ❌", style="red", justify="center")
            
            matrix = agreement_stats.get('matrix', {})
            
            # User satisfied row
            agree_table.add_row(
                "[green]User: ✅ Satisfied[/green]",
                str(matrix.get('sat_correct', 0)),
                str(matrix.get('sat_partial', 0)),
                str(matrix.get('sat_incorrect', 0))
            )
            # User partial row
            agree_table.add_row(
                "[yellow]User: 🔶 Partial[/yellow]",
                str(matrix.get('partial_correct', 0)),
                str(matrix.get('partial_partial', 0)),
                str(matrix.get('partial_incorrect', 0))
            )
            # User not satisfied row
            agree_table.add_row(
                "[red]User: ❌ Not Satisfied[/red]",
                str(matrix.get('notsat_correct', 0)),
                str(matrix.get('notsat_partial', 0)),
                str(matrix.get('notsat_incorrect', 0))
            )
            
            # Separator and insights
            agree_table.add_row("[dim]─────────────────────[/dim]", "[dim]─────────[/dim]", "[dim]─────────[/dim]", "[dim]─────────[/dim]")
            
            total = agreement_stats.get('total', 1)
            
            # Key insights
            user_demanding = agreement_stats.get('user_too_demanding', 0)
            user_fooled = agreement_stats.get('user_easily_fooled', 0)
            perfect_agree = agreement_stats.get('perfect_agreement', 0)
            
            agree_table.add_row(
                "🎯 Perfect Agreement",
                f"[cyan]{perfect_agree}[/cyan]",
                f"[cyan]{perfect_agree/total*100:.1f}%[/cyan]",
                "[dim]Both agree[/dim]"
            )
            agree_table.add_row(
                "😤 User Too Demanding",
                f"[yellow]{user_demanding}[/yellow]",
                f"[yellow]{user_demanding/total*100:.1f}%[/yellow]",
                "[dim]NOT_SAT but CORRECT[/dim]"
            )
            agree_table.add_row(
                "😅 User Easily Fooled",
                f"[red]{user_fooled}[/red]",
                f"[red]{user_fooled/total*100:.1f}%[/red]",
                "[dim]SAT but INCORRECT[/dim]"
            )
            
            self.console.print(agree_table)
        
        self.console.print("\n")
        
        # File logging
        if self.file_console:
            self.file_console.print("\n" + "="*70)
            self.file_console.print("  📊 PIPELINE SUMMARY")
            self.file_console.print("="*70)
            self.file_console.print(f"  Total Issues: {total_issues}")
            self.file_console.print(f"  Completed: {successful}")
            self.file_console.print(f"  Failed: {failed}")
            self.file_console.print(f"  Completion Rate: {success_rate:.1f}%")
            self.file_console.print(f"  Total Duration: {total_duration:.1f}s ({total_duration/60:.1f}m)")
            self.file_console.print(f"  Avg per Issue: {total_duration/total_issues:.1f}s" if total_issues > 0 else "  Avg per Issue: N/A")
            
            if generation_stats:
                self.file_console.print("\n  --- GENERATION PHASE ---")
                total_gen = generation_stats.get('fully_satisfied', 0) + \
                            generation_stats.get('partially_satisfied', 0) + \
                            generation_stats.get('not_satisfied', 0)
                if total_gen > 0:
                    self.file_console.print(f"  ✅ Fully Satisfied: {generation_stats.get('fully_satisfied', 0)} ({generation_stats.get('fully_satisfied', 0)/total_gen*100:.1f}%)")
                    self.file_console.print(f"  🔶 Partially Satisfied: {generation_stats.get('partially_satisfied', 0)} ({generation_stats.get('partially_satisfied', 0)/total_gen*100:.1f}%)")
                    self.file_console.print(f"  ❌ Not Satisfied: {generation_stats.get('not_satisfied', 0)} ({generation_stats.get('not_satisfied', 0)/total_gen*100:.1f}%)")
                    if 'avg_turns' in generation_stats:
                        self.file_console.print(f"  📊 Avg Turns: {generation_stats['avg_turns']:.1f}")
                    if generation_stats.get('avg_satisfaction_turn'):
                        self.file_console.print(f"  🎯 Avg Turn to Satisfaction: {generation_stats['avg_satisfaction_turn']:.1f}")
                    if 'max_turns_reached' in generation_stats:
                        self.file_console.print(f"  ⏱️ Hit Max Turns: {generation_stats['max_turns_reached']} ({generation_stats['max_turns_reached']/total_gen*100:.1f}%)")
            
            if evaluation_stats:
                self.file_console.print("\n  --- EVALUATION PHASE ---")
                total_eval = evaluation_stats.get('correct', 0) + \
                             evaluation_stats.get('partial', 0) + \
                             evaluation_stats.get('incorrect', 0)
                if total_eval > 0:
                    self.file_console.print(f"  ✅ Correct: {evaluation_stats.get('correct', 0)} ({evaluation_stats.get('correct', 0)/total_eval*100:.1f}%)")
                    self.file_console.print(f"  🔶 Partial: {evaluation_stats.get('partial', 0)} ({evaluation_stats.get('partial', 0)/total_eval*100:.1f}%)")
                    self.file_console.print(f"  ❌ Incorrect: {evaluation_stats.get('incorrect', 0)} ({evaluation_stats.get('incorrect', 0)/total_eval*100:.1f}%)")
                    if 'avg_alignment' in evaluation_stats:
                        self.file_console.print(f"  📈 Avg Alignment: {evaluation_stats['avg_alignment']:.1f}%")
            
            if agreement_stats and agreement_stats.get('total', 0) > 0:
                self.file_console.print("\n  --- USER ↔ JUDGE AGREEMENT ---")
                total = agreement_stats.get('total', 1)
                self.file_console.print(f"  🎯 Perfect Agreement: {agreement_stats.get('perfect_agreement', 0)} ({agreement_stats.get('perfect_agreement', 0)/total*100:.1f}%)")
                self.file_console.print(f"  😤 User Too Demanding: {agreement_stats.get('user_too_demanding', 0)} ({agreement_stats.get('user_too_demanding', 0)/total*100:.1f}%)")
                self.file_console.print(f"  😅 User Easily Fooled: {agreement_stats.get('user_easily_fooled', 0)} ({agreement_stats.get('user_easily_fooled', 0)/total*100:.1f}%)")
            
            self.file_console.print("="*70 + "\n")


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
