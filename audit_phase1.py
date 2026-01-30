#!/usr/bin/env python3
"""
Phase 1 Audit Script
====================

Comprehensive audit to identify:
- Code quality issues
- Potential bugs
- Missing error handling
- Performance bottlenecks
- Security concerns
"""

import ast
import os
import re
from pathlib import Path
from typing import List, Dict, Any

class CodeAuditor:
    def __init__(self, base_path: str = "."):
        self.base_path = Path(base_path)
        self.issues = []
        self.warnings = []
        self.suggestions = []
    
    def audit_file(self, filepath: Path):
        """Audit a single Python file"""
        print(f"\n{'='*60}")
        print(f"Auditing: {filepath}")
        print('='*60)
        
        with open(filepath, 'r') as f:
            content = f.read()
        
        # Parse AST
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            self.issues.append(f"SYNTAX ERROR in {filepath}: {e}")
            return
        
        # Check for issues
        self.check_error_handling(filepath, content, tree)
        self.check_logging(filepath, content)
        self.check_hardcoded_values(filepath, content)
        self.check_sql_injection(filepath, content)
        self.check_async_patterns(filepath, tree)
        self.check_resource_cleanup(filepath, content)
    
    def check_error_handling(self, filepath, content, tree):
        """Check for bare except clauses and missing error handling"""
        issues = []
        
        # Find bare except clauses
        bare_excepts = re.findall(r'except\s*:', content)
        if bare_excepts:
            issues.append(f"Found {len(bare_excepts)} bare 'except:' clauses - should specify exception type")
        
        # Find try blocks without logging
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # Check if exception is logged
                handler_code = ast.unparse(node)
                if 'logger' not in handler_code and 'logging' not in handler_code:
                    issues.append(f"Exception handler at line {node.lineno} doesn't log error")
        
        if issues:
            print(f"⚠️  Error Handling Issues:")
            for issue in issues:
                print(f"   - {issue}")
            self.warnings.extend([(filepath, issue) for issue in issues])
    
    def check_logging(self, filepath, content):
        """Check for print statements instead of logging"""
        issues = []
        
        # Find print statements
        prints = re.findall(r'\bprint\s*\(', content)
        if prints and 'test' not in str(filepath):
            issues.append(f"Found {len(prints)} print() statements - should use logger")
        
        # Check if logger is defined
        if 'import logging' in content or 'from logging' in content:
            if 'logger = logging.getLogger' not in content:
                issues.append("Imports logging but doesn't create logger instance")
        
        if issues:
            print(f"⚠️  Logging Issues:")
            for issue in issues:
                print(f"   - {issue}")
            self.warnings.extend([(filepath, issue) for issue in issues])
    
    def check_hardcoded_values(self, filepath, content):
        """Check for hardcoded values that should be config"""
        issues = []
        
        # Common hardcoded patterns
        if re.search(r'=\s*["\'](0x[0-9a-fA-F]+)["\']', content):
            if '.env' not in str(filepath) and 'test' not in str(filepath):
                issues.append("Found hardcoded hex values (wallet addresses?)")
        
        # Hardcoded URLs
        urls = re.findall(r'https?://[^\s\'"]+', content)
        if urls and 'config.py' not in str(filepath):
            issues.append(f"Found {len(urls)} hardcoded URLs - should be in config")
        
        if issues:
            print(f"ℹ️  Hardcoded Values:")
            for issue in issues:
                print(f"   - {issue}")
            self.suggestions.extend([(filepath, issue) for issue in issues])
    
    def check_sql_injection(self, filepath, content):
        """Check for potential SQL injection vulnerabilities"""
        issues = []
        
        # String formatting in SQL
        if re.search(r'execute\s*\(\s*f["\']', content):
            issues.append("CRITICAL: Found f-string in SQL execute() - SQL injection risk!")
        
        if re.search(r'execute\s*\(\s*.*%.*\)', content):
            issues.append("WARNING: Found % formatting in SQL - use parameterized queries")
        
        if issues:
            print(f"🔴 SECURITY Issues:")
            for issue in issues:
                print(f"   - {issue}")
            self.issues.extend([(filepath, issue) for issue in issues])
    
    def check_async_patterns(self, filepath, tree):
        """Check for common async antipatterns"""
        issues = []
        
        for node in ast.walk(tree):
            # Blocking calls in async functions
            if isinstance(node, ast.AsyncFunctionDef):
                func_code = ast.unparse(node)
                
                # Check for time.sleep instead of asyncio.sleep
                if 'time.sleep' in func_code:
                    issues.append(f"Async function '{node.name}' uses time.sleep() - should use asyncio.sleep()")
                
                # Check for requests instead of aiohttp
                if re.search(r'\brequests\.(get|post)', func_code):
                    issues.append(f"Async function '{node.name}' uses requests - should use aiohttp")
        
        if issues:
            print(f"⚠️  Async Pattern Issues:")
            for issue in issues:
                print(f"   - {issue}")
            self.warnings.extend([(filepath, issue) for issue in issues])
    
    def check_resource_cleanup(self, filepath, content):
        """Check for proper resource cleanup"""
        issues = []
        
        # Files opened without context manager
        if re.search(r'\bopen\s*\([^)]+\)(?!\s+as\s+)', content):
            # Check if it's not in a with statement
            if 'with open' not in content:
                issues.append("File opened without 'with' context manager - may leak file handles")
        
        # Database connections without context manager
        if 'sqlite3.connect' in content and 'with self._get_connection' not in content:
            lines_with_connect = [i for i, line in enumerate(content.split('\n'), 1) if 'sqlite3.connect' in line and 'def _get_connection' not in line]
            if lines_with_connect:
                issues.append(f"sqlite3.connect without context manager at lines: {lines_with_connect}")
        
        if issues:
            print(f"⚠️  Resource Cleanup Issues:")
            for issue in issues:
                print(f"   - {issue}")
            self.warnings.extend([(filepath, issue) for issue in issues])
    
    def run_audit(self):
        """Run audit on all Python files"""
        print("="*60)
        print("PHASE 1 COMPREHENSIVE AUDIT")
        print("="*60)
        
        # Find all Python files
        python_files = [
            'orchestrator.py',
            'executor.py',
            'config.py',
            'strategies/base_strategy.py',
            'strategies/sniper.py',
            'strategies/copy_trader.py',
            'storage/positions.py',
        ]
        
        for filepath in python_files:
            full_path = self.base_path / filepath
            if full_path.exists():
                self.audit_file(full_path)
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print audit summary"""
        print("\n" + "="*60)
        print("AUDIT SUMMARY")
        print("="*60)
        
        print(f"\n🔴 CRITICAL Issues: {len(self.issues)}")
        for filepath, issue in self.issues:
            print(f"   {filepath}: {issue}")
        
        print(f"\n⚠️  Warnings: {len(self.warnings)}")
        for filepath, issue in self.warnings[:10]:  # Show first 10
            print(f"   {filepath}: {issue}")
        if len(self.warnings) > 10:
            print(f"   ... and {len(self.warnings) - 10} more")
        
        print(f"\nℹ️  Suggestions: {len(self.suggestions)}")
        for filepath, issue in self.suggestions[:5]:  # Show first 5
            print(f"   {filepath}: {issue}")
        if len(self.suggestions) > 5:
            print(f"   ... and {len(self.suggestions) - 5} more")
        
        # Overall health score
        total = len(self.issues) + len(self.warnings) + len(self.suggestions)
        if total == 0:
            print("\n✅ Code Quality: EXCELLENT")
        elif len(self.issues) == 0 and len(self.warnings) < 5:
            print("\n✅ Code Quality: GOOD")
        elif len(self.issues) == 0:
            print("\n⚠️  Code Quality: FAIR - improvements recommended")
        else:
            print("\n🔴 Code Quality: NEEDS ATTENTION - critical issues found")

if __name__ == "__main__":
    auditor = CodeAuditor()
    auditor.run_audit()
