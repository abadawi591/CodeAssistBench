"""Command-line interface for CAB evaluation."""

import asyncio
import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from tqdm import tqdm
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn, TimeElapsedColumn
from rich.text import Text
from rich.live import Live
from rich.console import Group
from rich.panel import Panel

from .core.config import CABConfig
from .core.exceptions import AgentCorruptedError
from .utils.data_processor import DataProcessor
from .utils.rich_logger import CABLogger, get_cab_logger, console
from .workflows.cab_workflow import CABWorkflow


def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None):
    """Setup logging configuration with rich formatting.
    
    Args:
        log_level: Logging level
        log_file: Optional log file path
    """
    from rich.logging import RichHandler
    
    # Use RichHandler for beautiful console output
    handlers = [
        RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            markup=True,
            rich_tracebacks=True,
            log_time_format="[%H:%M:%S]"
        )
    ]
    
    if log_file:
        # Plain file handler (no rich formatting)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        ))
        handlers.append(file_handler)
    
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(message)s",
        handlers=handlers,
        force=True  # Override any existing configuration
    )
    
    # Suppress noisy third-party loggers
    noisy_loggers = [
        "azure",
        "azure.core",
        "azure.core.pipeline",
        "azure.core.pipeline.policies",
        "azure.identity",
        "azure.identity._credentials",
        "openai",
        "openai._base_client",
        "httpx",
        "httpcore",
        "urllib3",
        "urllib3.connectionpool",
        "asyncio",
        "charset_normalizer",
        "filelock",
        "msal",
        "strands",
        "strands.agent",
        "strands.models",
        "strands_tools",
    ]
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


async def run_dataset(args):
    """Run CAB evaluation on a dataset."""
    logger = logging.getLogger(__name__)
    
    # Load configuration  
    config = None
    if args.config:
        config = CABConfig.from_file(args.config)
    else:
        config = CABConfig()
    
    # Update config with OpenHands settings if provided
    if hasattr(args, 'openhands_config') and args.openhands_config:
        config.agent_framework.openhands_config_path = args.openhands_config
    
    # Update max conversation rounds if provided via CLI
    if hasattr(args, 'max_conversation_rounds') and args.max_conversation_rounds is not None:
        config.workflow.max_conversation_rounds = args.max_conversation_rounds
        logger.info(f"Setting max_conversation_rounds to {args.max_conversation_rounds} from CLI argument")
    
    # Parse agent model mapping
    agent_model_mapping = None
    if args.agent_models:
        try:
            agent_model_mapping = json.loads(args.agent_models)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in agent_models: {e}")
            return 1
    
    # Parse agent framework mapping
    agent_framework_mapping = None
    if hasattr(args, 'agent_framework') and args.agent_framework:
        try:
            agent_framework_mapping = json.loads(args.agent_framework)
            logger.debug(f"Agent frameworks: {agent_framework_mapping}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in agent_framework: {e}")
            return 1
    
    # Run dataset processing
    try:
        evaluator = CABWorkflow(config)
        summary = await evaluator.process_dataset(
            dataset_path=args.dataset_path,
            target_language=args.language,
            output_dir=args.output_dir,
            agent_model_mapping=agent_model_mapping,
            agent_framework_mapping=agent_framework_mapping,
            batch_size=args.batch_size,
            resume_processing=args.resume,
            enable_ast_tools=not getattr(args, 'disable_ast_tools', False)
        )
        
        logger.info(f"Dataset processing complete: {summary}")
        return 0
        
    except Exception as e:
        logger.error(f"Dataset processing failed: {e}")
        return 1


async def run_generation_dataset(args):
    """Run generation workflow on JSONL dataset."""
    logger = logging.getLogger(__name__)
    
    # Load configuration
    config = None
    if args.config:
        config = CABConfig.from_file(args.config)
    else:
        config = CABConfig()
    
    # Update config with OpenHands settings if provided
    if hasattr(args, 'openhands_config') and args.openhands_config:
        config.agent_framework.openhands_config_path = args.openhands_config
    
    # Update max conversation rounds if provided via CLI
    if hasattr(args, 'max_conversation_rounds') and args.max_conversation_rounds is not None:
        config.workflow.max_conversation_rounds = args.max_conversation_rounds
        logger.info(f"Setting max_conversation_rounds to {args.max_conversation_rounds} from CLI argument")
    
    # Parse agent model mapping
    agent_model_mapping = None
    if args.agent_models:
        try:
            agent_model_mapping = json.loads(args.agent_models)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in agent_models: {e}")
            return 1
    
    # Parse agent framework mapping
    agent_framework_mapping = None
    if hasattr(args, 'agent_framework') and args.agent_framework:
        try:
            agent_framework_mapping = json.loads(args.agent_framework)
            logger.debug(f"Agent frameworks: {agent_framework_mapping}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in agent_framework: {e}")
            return 1
    
    # Build complete framework mapping for tracking (including defaults)
    complete_framework_mapping = {
        "maintainer": agent_framework_mapping.get("maintainer", "strands") if agent_framework_mapping else "strands",
        "user": agent_framework_mapping.get("user", "strands") if agent_framework_mapping else "strands",
        "judge": agent_framework_mapping.get("judge", "strands") if agent_framework_mapping else "strands"
    }
    
    # Create output filename if not specified
    if not args.output:
        dataset_name = Path(args.dataset_file).stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Create folder structure: results/generation/
        output_dir = Path("results/generation")
        output_dir.mkdir(parents=True, exist_ok=True)
        args.output = str(output_dir / f"generation_results_{dataset_name}_{timestamp}.jsonl")
    
    # Load all issues from JSONL
    try:
        data_processor = DataProcessor()
        raw_data = data_processor.load_jsonl_data([args.dataset_file])
        
        # Filter by language if specified
        if args.language:
            raw_data = [item for item in raw_data if item.get('language', '').lower() == args.language.lower()]
            logger.info(f"Filtered to {len(raw_data)} issues for language: {args.language}")
        
        # Filter by classification category if specified
        if hasattr(args, 'category') and args.category:
            raw_data = [
                item for item in raw_data 
                if item.get('_classification', {}).get('category', '').lower() == args.category.lower()
            ]
            logger.info(f"Filtered to {len(raw_data)} issues for category: {args.category}")
        
        # Filter out Docker issues if --no-docker flag is set
        if hasattr(args, 'no_docker') and args.no_docker:
            raw_data = [item for item in raw_data if item.get('dockerfile') is None]
            logger.info(f"Filtered to {len(raw_data)} non-Docker issues")
        
        # Filter to only Docker issues if --has-dockerfile flag is set
        if hasattr(args, 'has_dockerfile') and args.has_dockerfile:
            raw_data = [item for item in raw_data if item.get('dockerfile') is not None]
            logger.info(f"Filtered to {len(raw_data)} Docker issues")
        
        # Convert to IssueData objects
        issues = []
        for i, item in enumerate(raw_data):
            try:
                issue_data = data_processor.load_issue_data_from_dict(item)
                issues.append(issue_data)
            except Exception as e:
                logger.warning(f"Failed to load issue {i}: {e}")
                continue
        
        logger.info(f"Loaded {len(issues)} valid issues from {args.dataset_file}")
        
    except Exception as e:
        logger.error(f"Error loading JSONL dataset: {e}")
        return 1
    
    # Check for resume functionality
    processed_issues = set()
    if args.resume and Path(args.output).exists():
        try:
            with open(args.output, 'r') as f:
                for line in f:
                    try:
                        result = json.loads(line.strip())
                        if 'issue_id' in result:
                            processed_issues.add(result['issue_id'])
                    except json.JSONDecodeError:
                        continue
            logger.info(f"Resuming: found {len(processed_issues)} already processed issues")
        except Exception as e:
            logger.warning(f"Error reading existing output file for resume: {e}")
    
    # Filter out already processed issues
    issues_to_process = [issue for issue in issues if issue.id not in processed_issues]
    
    # Apply limit if specified
    if hasattr(args, 'limit') and args.limit is not None:
        issues_to_process = issues_to_process[:args.limit]
        logger.info(f"Processing {len(issues_to_process)} issues (limited from {len(issues) - len(processed_issues)})")
    else:
        logger.info(f"Processing {len(issues_to_process)} issues (total: {len(issues)})")
    
    # Process issues and write results to JSONL
    try:
        from .workflows.generation_workflow import GenerationWorkflow
        import threading
        
        generation_workflow = GenerationWorkflow(config)
        successful_count = 0
        failed_count = 0
        start_time = time.time()
        
        # Concurrency settings
        concurrency = getattr(args, 'concurrency', 1)
        semaphore = asyncio.Semaphore(concurrency)
        write_lock = threading.Lock()
        
        # Initialize rich logger
        log_file_path = str(Path(args.output).parent / f"generation_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        cab_logger = get_cab_logger(log_file_path)
        cab_logger.info(f"Starting generation with concurrency: {concurrency}")
        cab_logger.info(f"Output file: {args.output}")
        cab_logger.info(f"Log file: {log_file_path}")
        
        # Warm up Azure config cache (avoids concurrent Key Vault lookups)
        if agent_model_mapping:
            maintainer_model = agent_model_mapping.get("maintainer", "gpt-5.2")
            if "gpt-5" in maintainer_model.lower() or "gpt5" in maintainer_model.lower():
                logger.info("Pre-caching Azure OpenAI configuration...")
                from .agents.strands_agent import StrandsAgent
                warmup_agent = StrandsAgent(model_name=maintainer_model, config=config)
                warmup_agent._get_azure_openai_config(maintainer_model)
                logger.info("Azure config cached. Ready for parallel processing.")
        
        # Open output file for appending (for resume functionality)
        mode = 'a' if (args.resume and Path(args.output).exists()) else 'w'
        output_file = open(args.output, mode)
        
        # Track active and completed issues
        # status can be: "cloning", "exploring", "user", "maintainer", "done"
        active_issues = {}  # {issue_id: {"title": str, "turn": int, "status": str, "phase": str, "start_time": float}}
        completed_issues = []  # List of {"issue_id": str, "title": str, "result": str, "turns": int, "duration": int}
        active_issues_lock = threading.Lock()
        
        def update_issue_progress(issue_id: str, title: str, turn: int, status: str = "processing", phase: str = None, result: str = None):
            """Update the progress for a specific issue."""
            with active_issues_lock:
                short_title = title[:50] + "..." if len(title) > 50 else title
                
                if status == "done":
                    # Move to completed list
                    if issue_id in active_issues:
                        issue_info = active_issues.pop(issue_id)
                        duration = int(time.time() - issue_info.get('start_time', time.time()))
                        completed_issues.append({
                            "issue_id": issue_id,
                            "title": short_title,
                            "result": result or "✓",
                            "turns": turn,
                            "duration": duration
                        })
                        # Keep only last 20 completed issues visible
                        while len(completed_issues) > 20:
                            completed_issues.pop(0)
                else:
                    if issue_id not in active_issues:
                        active_issues[issue_id] = {
                            "title": short_title, 
                            "turn": turn, 
                            "status": status, 
                            "phase": phase or status,
                            "start_time": time.time()
                        }
                    else:
                        update = {"title": short_title, "turn": turn, "status": status}
                        if phase:
                            update["phase"] = phase
                        active_issues[issue_id].update(update)
        
        def render_active_issues_table():
            """Render completed + active issues as a table."""
            from rich.table import Table
            
            with active_issues_lock:
                if not active_issues and not completed_issues:
                    return Text("Waiting for issues...", style="dim")
                
                table = Table(show_header=False, box=None, padding=(0, 1), collapse_padding=True)
                table.add_column("Issue", style="dim cyan", width=8)
                table.add_column("Status", style="bold", width=22)
                table.add_column("Title", style="white", overflow="ellipsis", max_width=50)
                table.add_column("Time", style="dim", width=8)
                
                # Show completed issues first (most recent at bottom)
                for completed in completed_issues:
                    result = completed.get('result', '✗')
                    # Map satisfaction status to display
                    if result == "FULLY_SATISFIED":
                        result_display = "[green]✓ SATISFIED[/green]"
                    elif result == "PARTIALLY_SATISFIED":
                        result_display = "[yellow]◐ PARTIAL[/yellow]"
                    elif result == "NOT_SATISFIED":
                        result_display = "[red]✗ NOT_SAT[/red]"
                    elif result == "AGENT_ERROR":
                        result_display = "[magenta]⚠ AGENT_ERR[/magenta]"
                    elif result == "✓":
                        result_display = "[green]✓ Done[/green]"
                    else:
                        result_display = "[red]✗ ERROR[/red]"
                    status_display = f"{result_display} T{completed['turns']}"
                    table.add_row(
                        f"[dim]#{completed['issue_id']}[/dim]",
                        f"[dim]{status_display}[/dim]",
                        f"[dim]{completed['title']}[/dim]",
                        f"[dim]{completed['duration']}s[/dim]"
                    )
                
                # Add separator if we have both completed and active
                if completed_issues and active_issues:
                    table.add_row("", "[dim]─────────────[/dim]", "", "")
                
                # Show ALL active issues
                for issue_id, info in list(active_issues.items()):
                    turn_num = info['turn']
                    status = info['status']
                    phase = info.get('phase', status)
                    elapsed = int(time.time() - info.get('start_time', time.time()))
                    
                    # Phase/Turn display
                    if phase == "init":
                        phase_display = "[dim]🔧 Init...[/dim]"
                    elif phase == "cloning":
                        phase_display = "[cyan]📦 Cloning...[/cyan]"
                    elif phase and phase.startswith("explore_"):
                        iter_num = phase.split("_")[1]
                        phase_display = f"[magenta]🔍 Explore {iter_num}/5[/magenta]"
                    elif phase == "exploring":
                        phase_display = "[magenta]🔍 Exploring[/magenta]"
                    elif phase == "commit":
                        phase_display = "[blue]🔗 Commit[/blue]"
                    elif status == "user":
                        phase_display = f"[yellow]👤 Turn {turn_num}[/yellow]"
                    elif status == "maintainer":
                        phase_display = f"[green]🤖 Turn {turn_num}[/green]"
                    elif turn_num > 0:
                        phase_display = f"[dim]Turn {turn_num}[/dim]"
                    else:
                        phase_display = f"[dim]⏳ {phase}[/dim]"
                    
                    time_display = f"{elapsed}s"
                    
                    table.add_row(f"#{issue_id}", phase_display, info['title'], time_display)
                
                return table
        
        # Main progress bar (simple, one line)
        progress = Progress(
            SpinnerColumn(),
            TextColumn(f"[bold cyan]Generation (×{concurrency})[/bold cyan]"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TextColumn("[green]✓{task.fields[success]}[/green] [red]✗{task.fields[failed]}[/red]"),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            console=console,
            expand=False,
        )
        
        # Create a combined display with progress bar and active issues table
        from rich.table import Table as RichTable
        
        class CombinedDisplay:
            """Combined display with progress bar and active issues."""
            def __rich__(self):
                from rich.console import Group as RichGroup
                return RichGroup(
                    progress,
                    Text(""),  # Spacer
                    render_active_issues_table()
                )
        
        combined = CombinedDisplay()
        live = Live(combined, console=console, refresh_per_second=2, transient=False)
        live.start()
        
        task_id = progress.add_task("processing", total=len(issues_to_process), success=0, failed=0)
        
        async def process_single_issue(issue_data, issue_index):
            """Process a single issue with semaphore for concurrency control."""
            nonlocal successful_count, failed_count
            
            async with semaphore:
                issue_start_time = time.time()
                issue_id_str = str(issue_data.id)
                
                # Update progress to show this issue starting
                update_issue_progress(issue_id_str, issue_data.first_question.title, 0, "starting", "queued")
                
                try:
                    # Define progress callback for phase and turn updates
                    def on_progress_update(turn_num: int = 0, role: str = None, phase: str = None):
                        """Callback for progress updates. Can be called with just phase, or with turn+role."""
                        status = role if role else (phase if phase else "processing")
                        update_issue_progress(issue_id_str, issue_data.first_question.title, turn_num, status, phase)
                    
                    # Run generation workflow with progress callback
                    result = await generation_workflow.run_generation(
                        issue_data, 
                        agent_model_mapping, 
                        agent_framework_mapping,
                        enable_ast_tools=not getattr(args, 'disable_ast_tools', False),
                        rich_logger=cab_logger,
                        progress_callback=on_progress_update
                    )
                    
                    # Get original issue data
                    original_issue = None
                    for orig_item in raw_data:
                        if str(orig_item.get('number')) == str(result.issue_data.id):
                            original_issue = orig_item
                            break
                    
                    # Calculate issue duration
                    issue_duration = time.time() - issue_start_time
                    
                    # Convert to dictionary
                    result_dict = {
                        'issue_id': result.issue_data.id,
                        'question_title': result.issue_data.first_question.title,
                        'question_body': result.issue_data.first_question.body,
                        'user': result.issue_data.first_question.user,
                        'language': result.issue_data.language,
                        'repository': result.issue_data.commit_info.repository,
                        'commit_sha': result.issue_data.commit_info.sha,
                        'final_answer': result.final_answer,
                        'user_satisfied': result.user_satisfied,
                        'satisfaction_status': result.satisfaction_status.value,
                        'satisfaction_reason': result.satisfaction_reason,
                        'total_conversation_rounds': result.total_conversation_rounds,
                        'original_comment_count': result.original_comment_count,
                        'conversation_history': [
                            {'role': msg.role, 'content': msg.content}
                            for msg in result.conversation_history
                        ],
                        'exploration_history': result.exploration_history,
                        'exploration_log': result.exploration_log,
                        'llm_call_counter': result.llm_call_counter,
                        'prompt_cache': result.prompt_cache,
                        'duration_seconds': issue_duration,
                        'original_metadata': original_issue if original_issue else {},
                        'processing_metadata': {
                            'workflow': 'generation_only',
                            'timestamp': datetime.now().isoformat(),
                            'agent_model_mapping': agent_model_mapping or {},
                            'agent_framework_mapping': complete_framework_mapping,
                            'input_file': args.dataset_file,
                            'language_filter': args.language
                        }
                    }
                    
                    # Thread-safe write
                    with write_lock:
                        output_file.write(json.dumps(result_dict, default=str) + '\n')
                        output_file.flush()
                        successful_count += 1
                        progress.update(task_id, advance=1, success=successful_count, failed=failed_count)
                    
                    # Mark as completed with satisfaction status
                    satisfaction_result = result.satisfaction_status.value if result.satisfaction_status else "FULLY_SATISFIED"
                    update_issue_progress(issue_id_str, issue_data.first_question.title, result.total_conversation_rounds, "done", result=satisfaction_result)
                    
                    # Log issue completion with conversation summary
                    cab_logger.issue_complete_with_conversation(
                        issue_id=str(issue_data.id),
                        title=issue_data.first_question.title,
                        success=True,
                        satisfaction_status=result.satisfaction_status.value,
                        total_turns=result.total_conversation_rounds,
                        duration_seconds=issue_duration,
                        conversation_history=result.conversation_history
                    )
                    
                    return result_dict
                    
                except AgentCorruptedError as e:
                    # Agent corruption is a specific failure type (Strands bug)
                    error_duration = time.time() - issue_start_time
                    cab_logger.error(f"Agent corrupted: {e}", issue_id=str(issue_data.id))
                    
                    # Mark as completed with AGENT_ERROR status
                    update_issue_progress(issue_id_str, issue_data.first_question.title, 0, "done", result="AGENT_ERROR")
                    
                    with write_lock:
                        failed_count += 1
                        progress.update(task_id, advance=1, success=successful_count, failed=failed_count)
                    
                    # Write error result with AGENT_ERROR status
                    original_issue = None
                    for orig_item in raw_data:
                        if str(orig_item.get('number')) == str(issue_data.id):
                            original_issue = orig_item
                            break
                    
                    error_result = {
                        'issue_id': issue_data.id,
                        'question_title': issue_data.first_question.title,
                        'question_body': issue_data.first_question.body,
                        'user': issue_data.first_question.user,
                        'language': issue_data.language,
                        'repository': issue_data.commit_info.repository,
                        'commit_sha': issue_data.commit_info.sha,
                        'error': str(e),
                        'user_satisfied': False,
                        'satisfaction_status': 'AGENT_ERROR',
                        'satisfaction_reason': f'Agent corrupted: {str(e)}',
                        'total_conversation_rounds': 0,
                        'duration_seconds': error_duration,
                        'original_metadata': original_issue if original_issue else {},
                        'processing_metadata': {
                            'workflow': 'generation_only',
                            'timestamp': datetime.now().isoformat(),
                            'error_occurred': True,
                            'error_type': 'agent_corrupted',
                            'error_message': str(e),
                            'input_file': args.dataset_file
                        }
                    }
                    with write_lock:
                        output_file.write(json.dumps(error_result, default=str) + '\n')
                        output_file.flush()
                    
                    return error_result
                    
                except Exception as e:
                    # Log error with rich formatting
                    error_duration = time.time() - issue_start_time
                    cab_logger.error(str(e), issue_id=str(issue_data.id))
                    
                    # Mark as completed (failure/error)
                    update_issue_progress(issue_id_str, issue_data.first_question.title, 0, "done", result="ERROR")
                    
                    with write_lock:
                        failed_count += 1
                        progress.update(task_id, advance=1, success=successful_count, failed=failed_count)
                    
                    # Write error result
                    original_issue = None
                    for orig_item in raw_data:
                        if str(orig_item.get('number')) == str(issue_data.id):
                            original_issue = orig_item
                            break
                    
                    error_result = {
                        'issue_id': issue_data.id,
                        'question_title': issue_data.first_question.title,
                        'question_body': issue_data.first_question.body,
                        'user': issue_data.first_question.user,
                        'language': issue_data.language,
                        'repository': issue_data.commit_info.repository,
                        'commit_sha': issue_data.commit_info.sha,
                        'error': str(e),
                        'user_satisfied': False,
                        'satisfaction_status': 'ERROR',
                        'satisfaction_reason': f'Processing failed: {str(e)}',
                        'total_conversation_rounds': 0,
                        'duration_seconds': error_duration,
                        'original_metadata': original_issue if original_issue else {},
                        'processing_metadata': {
                            'workflow': 'generation_only',
                            'timestamp': datetime.now().isoformat(),
                            'error_occurred': True,
                            'error_message': str(e),
                            'input_file': args.dataset_file
                        }
                    }
                    with write_lock:
                        output_file.write(json.dumps(error_result, default=str) + '\n')
                        output_file.flush()
                    
                    return error_result
        
        # Create tasks for all issues and run concurrently
        tasks = [
            process_single_issue(issue_data, i) 
            for i, issue_data in enumerate(issues_to_process)
        ]
        
        # Run all tasks with controlled concurrency (via semaphore)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Clean up
        live.stop()
        output_file.close()
        
        # Calculate total duration
        total_duration = time.time() - start_time
        
        # Collect generation statistics from completed issues
        generation_stats = {
            'fully_satisfied': 0,
            'partially_satisfied': 0,
            'not_satisfied': 0,
            'total_turns': 0,
            'max_turns_reached': 0,
            'count': 0,
            'satisfaction_turns': []  # Track turns when satisfaction was achieved
        }
        
        # Get max_turns from config (default 10)
        max_turns = getattr(config, 'max_turns', 10) if config else 10
        
        for result in results:
            if isinstance(result, dict) and 'satisfaction_status' in result:
                generation_stats['count'] += 1
                status = result.get('satisfaction_status', 'NOT_SATISFIED')
                turns = result.get('total_conversation_rounds', 0)
                
                if status == 'FULLY_SATISFIED':
                    generation_stats['fully_satisfied'] += 1
                    # Track the turn at which satisfaction was achieved
                    generation_stats['satisfaction_turns'].append(turns)
                elif status == 'PARTIALLY_SATISFIED':
                    generation_stats['partially_satisfied'] += 1
                    generation_stats['satisfaction_turns'].append(turns)
                else:  # NOT_SATISFIED or ERROR
                    generation_stats['not_satisfied'] += 1
                
                generation_stats['total_turns'] += turns
                
                if turns >= max_turns:
                    generation_stats['max_turns_reached'] += 1
        
        # Calculate averages and satisfaction turn stats
        if generation_stats['count'] > 0:
            generation_stats['avg_turns'] = generation_stats['total_turns'] / generation_stats['count']
        
        if generation_stats['satisfaction_turns']:
            generation_stats['avg_satisfaction_turn'] = sum(generation_stats['satisfaction_turns']) / len(generation_stats['satisfaction_turns'])
            generation_stats['min_satisfaction_turn'] = min(generation_stats['satisfaction_turns'])
            generation_stats['max_satisfaction_turn'] = max(generation_stats['satisfaction_turns'])
        
        # Collect valid results for results log (filter out exceptions)
        valid_results = [r for r in results if isinstance(r, dict) and 'satisfaction_status' in r]
        
        # Log all results in a formatted table
        if valid_results:
            cab_logger.results_log(valid_results)
        
        # Log final summary with rich formatting
        cab_logger.summary(
            total_issues=len(issues_to_process),
            successful=successful_count,
            failed=failed_count,
            total_duration=total_duration,
            generation_stats=generation_stats if generation_stats['count'] > 0 else None
        )
        
        cab_logger.info(f"Results saved to: {args.output}")
        cab_logger.info(f"Log saved to: {log_file_path}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Generation dataset processing failed: {e}")
        return 1


async def run_evaluation_dataset(args):
    """Run evaluation workflow on JSONL generation results."""
    logger = logging.getLogger(__name__)
    
    # Track start time for duration calculation
    start_time = time.time()
    
    # Load configuration
    config = None
    if args.config:
        config = CABConfig.from_file(args.config)
    
    # Parse agent model mapping
    agent_model_mapping = None
    if args.agent_models:
        try:
            agent_model_mapping = json.loads(args.agent_models)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in agent_models: {e}")
            return 1
    
    # Create output filename if not specified
    if not args.output:
        input_name = Path(args.generation_results_file).stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Create folder structure: results/evaluation/
        output_dir = Path("results/evaluation")
        output_dir.mkdir(parents=True, exist_ok=True)
        args.output = str(output_dir / f"evaluation_results_{input_name}_{timestamp}.jsonl")
    
    # Load all generation results from JSONL
    try:
        generation_results = []
        with open(args.generation_results_file, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    generation_dict = json.loads(line.strip())
                    generation_results.append(generation_dict)
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON on line {line_num}: {e}")
                    continue
        
        logger.info(f"Loaded {len(generation_results)} generation results from {args.generation_results_file}")
        
    except Exception as e:
        logger.error(f"Error loading generation results JSONL: {e}")
        return 1
    
    # Check for resume functionality
    processed_issues = set()
    if args.resume and Path(args.output).exists():
        try:
            with open(args.output, 'r') as f:
                for line in f:
                    try:
                        result = json.loads(line.strip())
                        if 'issue_id' in result:
                            processed_issues.add(result['issue_id'])
                    except json.JSONDecodeError:
                        continue
            logger.info(f"Resuming: found {len(processed_issues)} already processed evaluations")
        except Exception as e:
            logger.warning(f"Error reading existing output file for resume: {e}")
    
    # Filter out already processed results
    results_to_process = [result for result in generation_results if result.get('issue_id') not in processed_issues]
    logger.info(f"Processing {len(results_to_process)} evaluations (total: {len(generation_results)})")
    
    # Process generation results and write evaluation results to JSONL
    try:
        from .workflows.evaluation_workflow import EvaluationWorkflow
        from .core.models import GenerationResult, ConversationMessage, SatisfactionStatus, JudgeConfig
        
        # Create judge config for iterative evaluation if enabled
        judge_config = JudgeConfig() if getattr(args, 'iterative', False) else None
        evaluation_workflow = EvaluationWorkflow(config, judge_config=judge_config)
        
        # Log evaluation mode
        if judge_config:
            logger.info(f"🔄 Using iterative judge evaluation (max_iterations={judge_config.max_iterations}, repo_exploration={judge_config.enable_repository_exploration})")
        else:
            logger.info("📍 Using traditional single-iteration judge evaluation")
        data_processor = DataProcessor()
        successful_count = 0
        failed_count = 0
        
        # Open output file for appending (for resume functionality)
        mode = 'a' if (args.resume and Path(args.output).exists()) else 'w'
        with open(args.output, mode) as f:
            # Progress bar for evaluation
            pbar = tqdm(
                enumerate(results_to_process),
                total=len(results_to_process),
                desc="Evaluation",
                unit="issue",
                ncols=120,
                bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]'
            )
            
            for i, generation_dict in pbar:
                issue_id = generation_dict.get('issue_id', f'unknown_{i}')
                short_title = generation_dict.get('question_title', 'Unknown')[:35]
                pbar.set_postfix_str(f"✓{successful_count} ✗{failed_count} | {short_title}...")
                
                logger.info(f"Evaluating {i+1}/{len(results_to_process)}: {issue_id} - {generation_dict.get('question_title', 'Unknown')}")
                
                try:
                    # Use original_metadata directly for reconstruction (simpler and more reliable)
                    original_metadata = generation_dict.get('original_metadata', {})
                    
                    # Build issue_data_dict using original_metadata which has all the correct info
                    issue_data_dict = original_metadata.copy()  # Start with original data
                    
                    # Override with generation result data where needed
                    issue_data_dict.update({
                        'id': generation_dict['issue_id'],
                        'number': original_metadata.get('number', generation_dict['issue_id']),  # Ensure number field exists
                        'language': generation_dict.get('language', original_metadata.get('language', 'unknown')),
                        'title': generation_dict.get('question_title', original_metadata.get('title', '')),
                        'body': generation_dict.get('question_body', original_metadata.get('body', '')),
                        'author': generation_dict.get('user', original_metadata.get('author', 'unknown')),
                        'satisfaction_conditions': original_metadata.get('satisfaction_conditions', []),
                    })
                    
                    # Ensure we have the required fields for data processor
                    if 'url' not in issue_data_dict and 'repository' in generation_dict:
                        issue_data_dict['url'] = generation_dict['repository']
                    if 'commit_id' not in issue_data_dict and 'commit_sha' in generation_dict:
                        issue_data_dict['commit_id'] = generation_dict['commit_sha']
                    
                    issue_data = data_processor.load_issue_data_from_dict(issue_data_dict)
                    
                    # Reconstruct GenerationResult
                    conversation_history = []
                    for msg_dict in generation_dict.get('conversation_history', []):
                        conversation_history.append(
                            ConversationMessage(role=msg_dict['role'], content=msg_dict['content'])
                        )
                    
                    satisfaction_status = SatisfactionStatus(generation_dict.get('satisfaction_status', 'NOT_SATISFIED'))
                    
                    generation_result = GenerationResult(
                        issue_data=issue_data,
                        modified_dockerfile=generation_dict.get('modified_dockerfile'),
                        total_conversation_rounds=generation_dict.get('total_conversation_rounds', 0),
                        original_comment_count=generation_dict.get('original_comment_count', 0),
                        user_satisfied=generation_dict.get('user_satisfied', False),
                        exploration_history=generation_dict.get('exploration_history', []),
                        exploration_log=generation_dict.get('exploration_log', ''),
                        conversation_history=conversation_history,
                        llm_call_counter=generation_dict.get('llm_call_counter', {}),
                        satisfaction_status=satisfaction_status,
                        satisfaction_reason=generation_dict.get('satisfaction_reason', ''),
                        final_answer=generation_dict.get('final_answer', ''),
                        prompt_cache=generation_dict.get('prompt_cache', {})
                    )
                    
                    # Run evaluation workflow (iterative if judge_config is provided)
                    if judge_config:
                        # Use iterative evaluation with repository path
                        repository_path = generation_dict.get('original_metadata', {}).get('repository_path', str(Path.cwd()))
                        evaluation_result = await evaluation_workflow.run_iterative_evaluation(
                            generation_result, 
                            repository_path, 
                            agent_model_mapping
                        )
                    else:
                        # Use traditional single-iteration evaluation
                        evaluation_result = await evaluation_workflow.run_evaluation(generation_result, agent_model_mapping)
                    
                    # Extract agent framework mapping from generation results (preserve what was used in generation)
                    generation_framework_mapping = generation_dict.get('processing_metadata', {}).get('agent_framework_mapping', {})
                    
                    # Build complete framework mapping (add judge framework to generation mapping)
                    complete_eval_framework_mapping = generation_framework_mapping.copy() if generation_framework_mapping else {
                        "maintainer": "strands",
                        "user": "strands"
                    }
                    complete_eval_framework_mapping["judge"] = "strands"  # Judge always uses strands
                    
                    # Convert to dictionary with complete metadata
                    result_dict = {
                        # Core evaluation result data
                        'issue_id': issue_data.id,
                        'question_title': issue_data.first_question.title,
                        'question_body': issue_data.first_question.body,
                        'user': issue_data.first_question.user,
                        'language': issue_data.language,
                        'repository': issue_data.commit_info.repository,
                        'commit_sha': issue_data.commit_info.sha,
                        'final_answer': generation_result.final_answer,
                        'judgment': evaluation_result.judgment,
                        'verdict': evaluation_result.verdict.value,
                        'key_issues': evaluation_result.key_issues,
                        'is_iterative': evaluation_result.is_iterative,
                        'alignment_score': (
                            {
                                'satisfied': evaluation_result.alignment_score.satisfied,
                                'total': evaluation_result.alignment_score.total,
                                'percentage': evaluation_result.alignment_score.percentage,
                                'conditions': [
                                    {
                                        'number': cond.number,
                                        'satisfied': cond.satisfied,
                                        'description': cond.description
                                    }
                                    for cond in evaluation_result.alignment_score.conditions
                                ]
                            } if evaluation_result.alignment_score else None
                        ),
                        'docker_results': (
                            {
                                'success': evaluation_result.docker_results.success,
                                'logs': evaluation_result.docker_results.logs,
                                'test_commands': evaluation_result.docker_results.test_commands,
                                'error': evaluation_result.docker_results.error
                            } if evaluation_result.docker_results else None
                        ),
                        'llm_calls': evaluation_result.llm_calls,
                        'prompt_cache': evaluation_result.prompt_cache,
                        
                        # Iterative evaluation metadata (if available)
                        'iterative_evaluation': (
                            {
                                'iterations_completed': len(evaluation_result.iterative_evaluation.iterations),
                                'max_iterations': judge_config.max_iterations if judge_config else 1,
                                'total_evaluation_time_seconds': evaluation_result.iterative_evaluation.total_evaluation_time_seconds,
                                'stopped_early': evaluation_result.iterative_evaluation.stopped_early,
                                'early_stopping_reason': evaluation_result.iterative_evaluation.early_stopping_reason,
                                'confidence_progression': evaluation_result.iterative_evaluation.confidence_progression,
                                'repository_exploration': (
                                    {
                                        'files_read': evaluation_result.iterative_evaluation.repository_exploration.files_read,
                                        'exploration_time_seconds': evaluation_result.iterative_evaluation.repository_exploration.exploration_time_seconds,
                                        'total_files_found': evaluation_result.iterative_evaluation.repository_exploration.total_files_found
                                    } if evaluation_result.iterative_evaluation.repository_exploration else None
                                ),
                                'conversation_analysis': evaluation_result.iterative_evaluation.conversation_analysis,
                                'total_token_usage': evaluation_result.iterative_evaluation.total_token_usage
                            } if evaluation_result.iterative_evaluation else None
                        ),
                        
                        # Generation result metadata
                        'generation_metadata': {
                            'user_satisfied': generation_result.user_satisfied,
                            'satisfaction_status': generation_result.satisfaction_status.value,
                            'satisfaction_reason': generation_result.satisfaction_reason,
                            'total_conversation_rounds': generation_result.total_conversation_rounds,
                            'generation_llm_calls': generation_result.llm_call_counter
                        },
                        
                        # Original input metadata preservation
                        'original_metadata': generation_dict.get('original_metadata', {}),
                        
                        'processing_metadata': {
                            'workflow': 'evaluation_iterative' if judge_config else 'evaluation_only',
                            'timestamp': datetime.now().isoformat(),
                            'agent_model_mapping': agent_model_mapping or {},
                            'agent_framework_mapping': complete_eval_framework_mapping,
                            'input_file': args.generation_results_file,
                            'judge_config_used': (
                                {
                                    'max_iterations': judge_config.max_iterations,
                                    'enable_repository_exploration': judge_config.enable_repository_exploration,
                                    'enable_conversation_analysis': judge_config.enable_conversation_analysis,
                                    'confidence_threshold': judge_config.confidence_threshold,
                                    'early_stopping_enabled': judge_config.early_stopping_enabled
                                } if judge_config else None
                            )
                        }
                    }
                    
                    # Write result as JSONL line
                    f.write(json.dumps(result_dict, default=str) + '\n')
                    f.flush()  # Ensure immediate write for progress tracking
                    
                    successful_count += 1
                    logger.info(f"✅ Issue {issue_id} evaluated successfully - Verdict: {evaluation_result.verdict.value}")
                    
                except Exception as e:
                    logger.error(f"❌ Error evaluating issue {issue_id}: {e}")
                    
                    # Write error result to maintain JSONL consistency
                    error_result = {
                        'issue_id': issue_id,
                        'question_title': generation_dict.get('question_title', ''),
                        'question_body': generation_dict.get('question_body', ''),
                        'user': generation_dict.get('user', ''),
                        'language': generation_dict.get('language', ''),
                        'repository': generation_dict.get('repository', ''),
                        'commit_sha': generation_dict.get('commit_sha', ''),
                        'error': str(e),
                        'verdict': 'ERROR',
                        'judgment': f'Evaluation failed: {str(e)}',
                        'key_issues': [f'Processing error: {str(e)}'],
                        
                        # Preserve metadata even in error cases
                        'original_metadata': generation_dict.get('original_metadata', {}),
                        'generation_metadata': {
                            'user_satisfied': generation_dict.get('user_satisfied', False),
                            'satisfaction_status': generation_dict.get('satisfaction_status', 'UNKNOWN'),
                            'total_conversation_rounds': generation_dict.get('total_conversation_rounds', 0)
                        },
                        
                        'processing_metadata': {
                            'workflow': 'evaluation_only',
                            'timestamp': datetime.now().isoformat(),
                            'error_occurred': True,
                            'error_message': str(e),
                            'input_file': args.generation_results_file
                        }
                    }
                    f.write(json.dumps(error_result, default=str) + '\n')
                    f.flush()
                    
                    failed_count += 1
        
        # Calculate evaluation statistics
        evaluation_stats = {
            'correct': 0,
            'partial': 0,
            'incorrect': 0,
            'total_alignment': 0,
            'alignment_count': 0
        }
        
        # Also collect generation stats from the input data
        generation_stats = {
            'fully_satisfied': 0,
            'partially_satisfied': 0,
            'not_satisfied': 0,
            'total_turns': 0,
            'max_turns_reached': 0,
            'count': 0,
            'satisfaction_turns': []
        }
        
        # User-Judge agreement matrix
        agreement_stats = {
            'matrix': {
                'sat_correct': 0, 'sat_partial': 0, 'sat_incorrect': 0,
                'partial_correct': 0, 'partial_partial': 0, 'partial_incorrect': 0,
                'notsat_correct': 0, 'notsat_partial': 0, 'notsat_incorrect': 0
            },
            'user_too_demanding': 0,  # NOT_SAT but CORRECT
            'user_easily_fooled': 0,  # SAT but INCORRECT
            'perfect_agreement': 0,   # Both agree (SAT+CORRECT or NOTSAT+INCORRECT)
            'total': 0
        }
        
        # Re-read results to collect statistics
        try:
            with open(args.output, 'r') as f:
                for line in f:
                    try:
                        result = json.loads(line)
                        
                        # Evaluation stats
                        verdict = result.get('verdict', '').upper()
                        if verdict == 'CORRECT':
                            evaluation_stats['correct'] += 1
                        elif verdict == 'PARTIAL':
                            evaluation_stats['partial'] += 1
                        elif verdict in ['INCORRECT', 'ERROR']:
                            evaluation_stats['incorrect'] += 1
                        
                        # Alignment score
                        alignment = result.get('alignment_score', {})
                        if isinstance(alignment, dict) and 'percentage' in alignment:
                            evaluation_stats['total_alignment'] += alignment['percentage']
                            evaluation_stats['alignment_count'] += 1
                        
                        # Generation stats from input
                        gen_meta = result.get('generation_metadata', {})
                        if gen_meta:
                            generation_stats['count'] += 1
                            status = gen_meta.get('satisfaction_status', 'NOT_SATISFIED')
                            turns = gen_meta.get('total_conversation_rounds', 0)
                            
                            if status == 'FULLY_SATISFIED':
                                generation_stats['fully_satisfied'] += 1
                                generation_stats['satisfaction_turns'].append(turns)
                            elif status == 'PARTIALLY_SATISFIED':
                                generation_stats['partially_satisfied'] += 1
                                generation_stats['satisfaction_turns'].append(turns)
                            else:
                                generation_stats['not_satisfied'] += 1
                            
                            generation_stats['total_turns'] += turns
                            if turns >= 10:  # Default max turns
                                generation_stats['max_turns_reached'] += 1
                            
                            # User-Judge agreement matrix
                            agreement_stats['total'] += 1
                            
                            # Map user status to matrix key prefix
                            if status == 'FULLY_SATISFIED':
                                user_key = 'sat'
                            elif status == 'PARTIALLY_SATISFIED':
                                user_key = 'partial'
                            else:
                                user_key = 'notsat'
                            
                            # Map judge verdict to matrix key suffix
                            if verdict == 'CORRECT':
                                judge_key = 'correct'
                            elif verdict == 'PARTIAL':
                                judge_key = 'partial'
                            else:
                                judge_key = 'incorrect'
                            
                            matrix_key = f'{user_key}_{judge_key}'
                            agreement_stats['matrix'][matrix_key] = agreement_stats['matrix'].get(matrix_key, 0) + 1
                            
                            # Calculate agreement insights
                            # User too demanding: NOT_SAT but judge says CORRECT
                            if status == 'NOT_SATISFIED' and verdict == 'CORRECT':
                                agreement_stats['user_too_demanding'] += 1
                            
                            # User easily fooled: FULLY_SATISFIED but judge says INCORRECT
                            if status == 'FULLY_SATISFIED' and verdict == 'INCORRECT':
                                agreement_stats['user_easily_fooled'] += 1
                            
                            # Perfect agreement: both positive or both negative
                            if (status == 'FULLY_SATISFIED' and verdict == 'CORRECT') or \
                               (status == 'NOT_SATISFIED' and verdict == 'INCORRECT'):
                                agreement_stats['perfect_agreement'] += 1
                                
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning(f"Could not collect statistics from output file: {e}")
        
        # Calculate averages
        if evaluation_stats['alignment_count'] > 0:
            evaluation_stats['avg_alignment'] = evaluation_stats['total_alignment'] / evaluation_stats['alignment_count']
        
        if generation_stats['count'] > 0:
            generation_stats['avg_turns'] = generation_stats['total_turns'] / generation_stats['count']
        
        if generation_stats['satisfaction_turns']:
            generation_stats['avg_satisfaction_turn'] = sum(generation_stats['satisfaction_turns']) / len(generation_stats['satisfaction_turns'])
            generation_stats['min_satisfaction_turn'] = min(generation_stats['satisfaction_turns'])
            generation_stats['max_satisfaction_turn'] = max(generation_stats['satisfaction_turns'])
        
        # Use rich logger for summary if available
        from .utils.rich_logger import get_cab_logger
        cab_logger = get_cab_logger()
        
        total_duration = time.time() - start_time
        
        cab_logger.summary(
            total_issues=len(results_to_process),
            successful=successful_count,
            failed=failed_count,
            total_duration=total_duration,
            generation_stats=generation_stats if generation_stats['count'] > 0 else None,
            evaluation_stats=evaluation_stats if (evaluation_stats['correct'] + evaluation_stats['partial'] + evaluation_stats['incorrect']) > 0 else None,
            agreement_stats=agreement_stats if agreement_stats['total'] > 0 else None
        )
        
        cab_logger.info(f"Results saved to: {args.output}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Evaluation dataset processing failed: {e}")
        return 1


def create_config_template(args):
    """Create a configuration template file."""
    config = CABConfig()
    
    try:
        config.save_to_file(args.output)
        print(f"Configuration template created at: {args.output}")
        return 0
    except Exception as e:
        print(f"Error creating config template: {e}")
        return 1


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CAB Evaluation - Code Agent Benchmark evaluation package"
    )
    
    # Global arguments
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set logging level"
    )
    parser.add_argument(
        "--log-file",
        help="Log file path"
    )
    parser.add_argument(
        "--config",
        help="Configuration file path"
    )
    parser.add_argument(
        "--agent-framework",
        help='JSON mapping of agents to frameworks (e.g., \'{"maintainer": "openhands"}\')'
    )
    parser.add_argument(
        "--openhands-config",
        help="Path to OpenHands config.toml file (required if using OpenHands framework)"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Dataset command - Complete CAB evaluation on JSONL dataset
    dataset_parser = subparsers.add_parser("dataset", help="Run complete CAB evaluation on JSONL dataset")
    dataset_parser.add_argument(
        "dataset_path",
        help="Path to JSONL dataset file"
    )
    dataset_parser.add_argument(
        "--language", "-l",
        help="Filter by programming language"
    )
    dataset_parser.add_argument(
        "--output-dir", "-o",
        default="results",
        help="Output directory for results"
    )
    dataset_parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=10,
        help="Batch size for processing"
    )
    dataset_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from previous processing (skip already processed issues)"
    )
    dataset_parser.add_argument(
        "--agent-models",
        help='JSON mapping of agents to models'
    )
    dataset_parser.add_argument(
        "--agent-framework",
        help='JSON mapping of agents to frameworks (e.g., \'{"maintainer": "openhands"}\' or \'{"maintainer": "qcli"}\')'
    )
    dataset_parser.add_argument(
        "--openhands-config",
        help="Path to OpenHands config.toml file (only used if maintainer uses OpenHands framework)"
    )
    dataset_parser.add_argument(
        "--max-conversation-rounds",
        type=int,
        help="Maximum conversation rounds between maintainer and user agents (default: 2)"
    )
    dataset_parser.add_argument(
        "--disable-ast-tools",
        action="store_true",
        help="Disable AST tools (read_code, edit_code) for OpenHands maintainer agent"
    )
    dataset_parser.add_argument(
        "--ast-system-prompt",
        type=str,
        default=None,
        help="Path to custom system prompt template for AST-focused OpenHands agent"
    )
    
    # Generation dataset command for JSONL files
    generation_dataset_parser = subparsers.add_parser("generation-dataset", help="Run generation workflow on JSONL dataset")
    generation_dataset_parser.add_argument(
        "dataset_file",
        help="JSONL file containing multiple issues"
    )
    generation_dataset_parser.add_argument(
        "--output", "-o",
        help="Output JSONL file for results (default: generation_results_<timestamp>.jsonl)"
    )
    generation_dataset_parser.add_argument(
        "--language", "-l",
        help="Filter by programming language"
    )
    generation_dataset_parser.add_argument(
        "--agent-models",
        help='JSON mapping of agents to models (e.g., \'{"maintainer": "sonnet37", "user": "haiku"}\')'
    )
    generation_dataset_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume processing (skip already processed issues)"
    )
    generation_dataset_parser.add_argument(
        "--agent-framework",
        help='JSON mapping of agents to frameworks (e.g., \'{"maintainer": "openhands"}\')'
    )
    generation_dataset_parser.add_argument(
        "--openhands-config",
        help="Path to OpenHands config.toml file (only used if maintainer uses OpenHands framework)"
    )
    generation_dataset_parser.add_argument(
        "--max-conversation-rounds",
        type=int,
        help="Maximum conversation rounds between maintainer and user agents (default: 2)"
    )
    generation_dataset_parser.add_argument(
        "--disable-ast-tools",
        action="store_true",
        help="Disable AST tools (read_code, edit_code) for OpenHands maintainer agent"
    )
    generation_dataset_parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=1,
        help="Number of issues to process concurrently (default: 1, recommended: 4-8 with multi-endpoint router)"
    )
    generation_dataset_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=None,
        help="Limit the number of issues to process (default: process all)"
    )
    generation_dataset_parser.add_argument(
        "--category",
        help='Filter by classification category (e.g., "Does not need build environment")'
    )
    generation_dataset_parser.add_argument(
        "--no-docker",
        action="store_true",
        help="Only process issues that don't require Docker (dockerfile is null)"
    )
    generation_dataset_parser.add_argument(
        "--has-dockerfile",
        action="store_true",
        help="Only process issues that have a Dockerfile (require build environment)"
    )
    
    # Evaluation dataset command for JSONL files with generation results
    evaluation_dataset_parser = subparsers.add_parser("evaluation-dataset", help="Run evaluation workflow on JSONL generation results")
    evaluation_dataset_parser.add_argument(
        "generation_results_file",
        help="JSONL file containing multiple generation results"
    )
    evaluation_dataset_parser.add_argument(
        "--output", "-o",
        help="Output JSONL file for evaluation results (default: evaluation_results_<timestamp>.jsonl)"
    )
    evaluation_dataset_parser.add_argument(
        "--agent-models",
        help='JSON mapping for judge agent model (e.g., \'{"judge": "sonnet"}\')'
    )
    evaluation_dataset_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume processing (skip already processed issues)"
    )
    evaluation_dataset_parser.add_argument(
        "--iterative",
        action="store_true",
        help="Enable iterative judge evaluation with repository exploration and conversation analysis"
    )
    
    # Config template command
    config_parser = subparsers.add_parser("config", help="Create configuration template")
    config_parser.add_argument(
        "output",
        help="Output path for config template"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.log_level, args.log_file)
    
    # Run appropriate command
    if args.command == "dataset":
        return asyncio.run(run_dataset(args))
    elif args.command == "generation-dataset":
        return asyncio.run(run_generation_dataset(args))
    elif args.command == "evaluation-dataset":
        return asyncio.run(run_evaluation_dataset(args))
    elif args.command == "config":
        return create_config_template(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    exit(main())
