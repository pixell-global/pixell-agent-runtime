import re
import ast
from typing import List, Set
import structlog

logger = structlog.get_logger()


class CodePatternDetector:
    """Detect patterns in Python code for optimization."""
    
    def __init__(self):
        self.import_patterns = {
            'pandas': ['pandas', 'pd'],
            'numpy': ['numpy', 'np'],
            'matplotlib': ['matplotlib', 'pyplot', 'plt'],
            'sklearn': ['sklearn', 'scikit-learn'],
            'torch': ['torch', 'pytorch'],
            'tensorflow': ['tensorflow', 'tf'],
            'polars': ['polars', 'pl'],
            'duckdb': ['duckdb'],
        }
        
        self.operation_patterns = {
            'data_loading': [
                r'read_csv', r'read_excel', r'read_json', r'read_parquet',
                r'loadtxt', r'load', r'open\s*\('
            ],
            'data_processing': [
                r'groupby', r'merge', r'concat', r'pivot', r'melt',
                r'apply', r'transform', r'agg', r'resample'
            ],
            'ml_training': [
                r'fit\s*\(', r'train\s*\(', r'compile\s*\(', r'backward\s*\(',
                r'optimizer\.step', r'model\.train'
            ],
            'visualization': [
                r'plot\s*\(', r'scatter\s*\(', r'bar\s*\(', r'hist\s*\(',
                r'imshow\s*\(', r'figure\s*\(', r'subplot'
            ],
            'large_data': [
                r'chunksize', r'iterator=True', r'low_memory',
                r'dask', r'ray', r'spark'
            ],
            'sql': [
                r'SELECT', r'FROM', r'WHERE', r'JOIN', r'GROUP BY',
                r'\.sql\s*\(', r'execute\s*\('
            ]
        }
    
    def detect(self, code: str) -> List[str]:
        """Detect patterns in code."""
        patterns = []
        
        # Detect imports
        imports = self._detect_imports(code)
        patterns.extend(imports)
        
        # Detect operations
        operations = self._detect_operations(code)
        patterns.extend(operations)
        
        # Detect complexity indicators
        complexity = self._detect_complexity(code)
        patterns.extend(complexity)
        
        return list(set(patterns))
    
    def _detect_imports(self, code: str) -> List[str]:
        """Detect imported libraries."""
        detected = []
        
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for pattern_name, keywords in self.import_patterns.items():
                            if any(kw in alias.name for kw in keywords):
                                detected.append(pattern_name)
                                
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        for pattern_name, keywords in self.import_patterns.items():
                            if any(kw in node.module for kw in keywords):
                                detected.append(pattern_name)
        except:
            # Fallback to regex if AST parsing fails
            for pattern_name, keywords in self.import_patterns.items():
                for kw in keywords:
                    if re.search(rf'import\s+{kw}|from\s+{kw}', code):
                        detected.append(pattern_name)
        
        return detected
    
    def _detect_operations(self, code: str) -> List[str]:
        """Detect operation patterns."""
        detected = []
        
        for pattern_name, regexes in self.operation_patterns.items():
            for regex in regexes:
                if re.search(regex, code, re.IGNORECASE):
                    detected.append(pattern_name)
                    break
        
        return detected
    
    def _detect_complexity(self, code: str) -> List[str]:
        """Detect complexity indicators."""
        indicators = []
        
        # Line count
        line_count = len(code.splitlines())
        if line_count > 100:
            indicators.append('complex_code')
        elif line_count > 50:
            indicators.append('medium_complexity')
        
        # Loop depth
        loop_depth = self._get_max_loop_depth(code)
        if loop_depth > 2:
            indicators.append('nested_loops')
        
        # Function count
        try:
            tree = ast.parse(code)
            func_count = sum(1 for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
            if func_count > 5:
                indicators.append('many_functions')
        except:
            pass
        
        return indicators
    
    def _get_max_loop_depth(self, code: str) -> int:
        """Get maximum loop nesting depth."""
        try:
            tree = ast.parse(code)
            return self._calculate_loop_depth(tree)
        except:
            return 0
    
    def _calculate_loop_depth(self, node: ast.AST, current_depth: int = 0) -> int:
        """Recursively calculate loop depth."""
        max_depth = current_depth
        
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.For, ast.While)):
                child_depth = self._calculate_loop_depth(child, current_depth + 1)
                max_depth = max(max_depth, child_depth)
            else:
                child_depth = self._calculate_loop_depth(child, current_depth)
                max_depth = max(max_depth, child_depth)
        
        return max_depth