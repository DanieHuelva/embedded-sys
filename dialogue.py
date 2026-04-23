"""
dialog_engine.py
Parses a TangoChat-style DSL script and performs rule matching.
Produces (speak_text, [action_tags]) for each user input.
"""

import re
import random
import threading
from enum import Enum, auto


# ---------------------------------------------------------------------------
# State machine states
# ---------------------------------------------------------------------------
class State(Enum):
    BOOT = auto()
    IDLE = auto()
    IN_SCOPE = auto()
    EXEC_ACTIONS = auto()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
class Rule:
    def __init__(self, level: int, pattern: str, output: str, children=None, line_no: int = 0):
        self.level = level          # 0 = u, 1 = u1, 2 = u2, ...
        self.pattern = pattern      # raw pattern string
        self.output = output        # raw output string
        self.children: list[Rule] = children or []
        self.line_no = line_no


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------

def _parse_bracket_options(text: str) -> list[str]:
    """
    Parse [word1 "two words" word3] -> ['word1', 'two words', 'word3']
    """
    options = []
    i = 0
    current = ""
    in_quote = False
    while i < len(text):
        ch = text[i]
        if ch == '"':
            in_quote = not in_quote
            if not in_quote and current:
                options.append(current.strip())
                current = ""
        elif ch == ' ' and not in_quote:
            if current.strip():
                options.append(current.strip())
            current = ""
        else:
            current += ch
        i += 1
    if current.strip():
        options.append(current.strip())
    return [o for o in options if o]


def _expand_definitions(text: str, definitions: dict) -> str:
    """Replace ~name references with their definition string."""
    def replacer(m):
        name = m.group(1)
        if name in definitions:
            return "[" + " ".join(f'"{o}"' if " " in o else o for o in definitions[name]) + "]"
        return m.group(0)
    return re.sub(r'~(\w+)', replacer, text)


def _pattern_to_regex(pattern: str, definitions: dict) -> re.Pattern:
    """
    Convert a DSL pattern string to a compiled regex.
    Handles: plain text, [choices], "quoted phrases", _ wildcard capture, ~definitions
    """
    # Expand definitions first
    expanded = _expand_definitions(pattern, definitions)

    # We'll build the regex piece by piece
    result = ""
    i = 0
    capture_index = [0]

    while i < len(expanded):
        ch = expanded[i]

        if ch == '[':
            # Find matching ]
            end = expanded.index(']', i)
            inner = expanded[i+1:end]
            options = _parse_bracket_options(inner)
            escaped = [re.escape(o) for o in options]
            result += "(?:" + "|".join(escaped) + ")"
            i = end + 1

        elif ch == '_':
            # Wildcard capture — capture one or more words
            result += r"(.+?)"
            capture_index[0] += 1
            i += 1

        elif ch == '"':
            # Quoted phrase — match literally
            end = expanded.index('"', i+1)
            phrase = expanded[i+1:end]
            result += re.escape(phrase)
            i = end + 1

        else:
            result += re.escape(ch)
            i += 1

    # Allow extra words around the pattern (partial match)
    full_pattern = r"(?:.*\s)?" + result.strip() + r"(?:\s.*)?"
    return re.compile(full_pattern, re.IGNORECASE)


def _resolve_output(output: str, definitions: dict, variables: dict, rng: random.Random) -> tuple[str, list[str]]:
    """
    Process output string:
    - Pick random option from [...]
    - Replace ~definitions with a random pick
    - Replace $var references
    - Extract <action_tags>
    Returns (spoken_text, [action_names])
    """
    # Extract action tags
    actions = re.findall(r'<(\w+)>', output)
    text = re.sub(r'<\w+>', '', output).strip()

    # Expand ~definitions in output
    def expand_def(m):
        name = m.group(1)
        if name in definitions:
            return rng.choice(definitions[name])
        return m.group(0)
    text = re.sub(r'~(\w+)', expand_def, text)

    # Resolve bracket choices [a b "c d"]
    def resolve_bracket(m):
        inner = m.group(1)
        options = _parse_bracket_options(inner)
        if options:
            return rng.choice(options)
        return ""
    text = re.sub(r'\[([^\]]*)\]', resolve_bracket, text)

    # Replace $var references
    def resolve_var(m):
        var_name = m.group(1)
        return variables.get(var_name, "I don't know")
    text = re.sub(r'\$(\w+)', resolve_var, text)

    return text.strip(), actions


# ---------------------------------------------------------------------------
# Main DialogEngine
# ---------------------------------------------------------------------------
class DialogEngine:

    KNOWN_ACTIONS = {"head_yes", "head_no", "arm_raise", "dance90"}

    def __init__(self, script_path: str, seed: int = None):
        self.script_path = script_path
        self.rng = random.Random(seed)
        self.definitions: dict[str, list[str]] = {}
        self.top_rules: list[Rule] = []
        self.variables: dict[str, str] = {}

        # State machine
        self._state = State.BOOT
        self._current_scope_rules: list[Rule] = []  # active subrules
        self._scope_depth = 0
        self._unmatched_in_scope = 0
        self._lock = threading.Lock()

        self._parse(script_path)
        self._transition(State.IDLE)
        print(f"[DialogEngine] Loaded {len(self.top_rules)} top-level rules, "
              f"{len(self.definitions)} definitions.")

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _transition(self, new_state: State, depth: int = 0):
        old = self._state
        self._state = new_state
        if new_state == State.IN_SCOPE:
            self._scope_depth = depth
            print(f"[STATE] {old.name} -> IN_SCOPE({depth})")
        else:
            self._scope_depth = 0
            print(f"[STATE] {old.name} -> {new_state.name}")

    def get_state(self) -> str:
        if self._state == State.IN_SCOPE:
            return f"IN_SCOPE({self._scope_depth})"
        return self._state.name

    # ------------------------------------------------------------------
    # Parser
    # ------------------------------------------------------------------

    def _parse(self, path: str):
        KNOWN_ACTIONS_SET = self.KNOWN_ACTIONS
        fatal = False

        try:
            with open(path, 'r') as f:
                raw_lines = f.readlines()
        except FileNotFoundError:
            print(f"[FATAL] Script file not found: {path}")
            raise

        lines = []
        for i, line in enumerate(raw_lines, 1):
            stripped = line.rstrip('\n')
            # Strip comments
            comment_pos = stripped.find('#')
            if comment_pos >= 0:
                stripped = stripped[:comment_pos]
            stripped = stripped.strip()
            if stripped:
                lines.append((i, stripped, line))  # (line_no, content, original)

        # --- Pass 1: definitions ---
        remaining = []
        for line_no, content, original in lines:
            def_match = re.match(r'^~(\w+)\s*:\s*\[(.+)\]', content)
            if def_match:
                name = def_match.group(1)
                options = _parse_bracket_options(def_match.group(2))
                if not options:
                    print(f"[WARN] {path}:{line_no}: Empty definition ~{name}, skipping.")
                    continue
                self.definitions[name] = options
                print(f"[PARSE] Definition ~{name} = {options}")
            elif re.match(r'^~\w+\s', content) or re.match(r'^~\w+$', content):
                # Bad definition line
                print(f"[ERROR] {path}:{line_no}: NON-FATAL: Bad definition syntax, skipping: {content}")
            else:
                remaining.append((line_no, content))

        # --- Pass 2: rules ---
        # We need to handle indentation to determine nesting
        # Re-read with indentation
        raw_rule_lines = []
        for line in raw_lines:
            line_no_orig = raw_lines.index(line) + 1
            stripped_comment = line.rstrip('\n')
            comment_pos = stripped_comment.find('#')
            if comment_pos >= 0:
                stripped_comment = stripped_comment[:comment_pos]
            content = stripped_comment.rstrip()
            if not content.strip():
                continue
            if content.strip().startswith('~'):
                continue  # handled above
            raw_rule_lines.append((line_no_orig, content))

        # Parse rules with indentation stack
        rule_stack: list[tuple[int, Rule]] = []  # (indent_level, rule)
        top_rules: list[Rule] = []

        for line_no, content in raw_rule_lines:
            indent = len(content) - len(content.lstrip())
            stripped = content.strip()

            # Match rule: u:(...):... or u1:(...):... etc
            rule_match = re.match(
                r'^(u\d*)\s*:\s*\(([^)]*)\)\s*:\s*(.*)',
                stripped
            )
            if not rule_match:
                # Check for missing second colon (common error)
                bad_match = re.match(r'^(u\d*)\s*:\s*\(([^)]*)\)\s+(\S.*)', stripped)
                if bad_match:
                    print(f"[ERROR] {self.script_path}:{line_no}: NON-FATAL: Missing ':' after pattern, skipping: {stripped}")
                else:
                    # Could be unbalanced brackets or other issues
                    if re.match(r'^u\d*\s*:', stripped):
                        print(f"[ERROR] {self.script_path}:{line_no}: NON-FATAL: Malformed rule, skipping: {stripped}")
                continue

            level_str = rule_match.group(1)
            pattern = rule_match.group(2).strip()
            output = rule_match.group(3).strip()

            # Determine numeric level
            if level_str == 'u':
                level = 0
            else:
                level = int(level_str[1:])

            # Check for unbalanced brackets in output
            if output.count('[') != output.count(']'):
                print(f"[ERROR] {self.script_path}:{line_no}: NON-FATAL: Unbalanced brackets in output, skipping: {stripped}")
                continue

            # Check for unknown action tags
            tags_in_output = re.findall(r'<(\w+)>', output)
            for tag in tags_in_output:
                if tag not in KNOWN_ACTIONS_SET:
                    print(f"[WARN] {self.script_path}:{line_no}: Unknown action tag <{tag}>, will be ignored at runtime.")

            rule = Rule(level=level, pattern=pattern, output=output, line_no=line_no)
            print(f"[PARSE] Rule L{level} line {line_no}: ({pattern}) -> {output[:40]}...")

            # Place in tree
            if level == 0:
                top_rules.append(rule)
                rule_stack = [(indent, rule)]
            else:
                # Find parent: walk back stack to find a rule with lower level
                while rule_stack and rule_stack[-1][1].level >= level:
                    rule_stack.pop()
                if rule_stack:
                    parent = rule_stack[-1][1]
                    parent.children.append(rule)
                    rule_stack.append((indent, rule))
                else:
                    print(f"[ERROR] {self.script_path}:{line_no}: NON-FATAL: Subrule u{level} has no parent, skipping.")
                    continue

        self.top_rules = top_rules

        if not self.top_rules:
            print(f"[FATAL] {path}: No valid top-level u: rules found. Refusing to run.")
            raise ValueError("No valid top-level rules in script.")

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _normalize_input(self, text: str) -> str:
        """Lowercase, strip basic punctuation."""
        text = text.lower()
        text = re.sub(r'[.,!?]', '', text)
        return text.strip()

    def _match_rule(self, rule: Rule, user_input: str) -> tuple[bool, dict]:
        """Try to match user_input against rule.pattern. Returns (matched, captured_vars)."""
        pattern = rule.pattern.strip()
        regex = _pattern_to_regex(pattern, self.definitions)
        m = regex.fullmatch(user_input)
        if m:
            # Extract captured variable
            captures = m.groups()
            captured = {}
            if captures:
                # The pattern uses _ for capture; the variable name comes from the output $varname
                var_names = re.findall(r'\$(\w+)', rule.output)
                for i, val in enumerate(captures):
                    if i < len(var_names):
                        captured[var_names[i]] = val.strip()
            return True, captured
        return False, {}

    def _is_interrupt(self, user_input: str) -> bool:
        words = user_input.lower().split()
        return any(w in {"stop", "cancel", "reset", "quit"} for w in words)

    def process_input(self, user_input: str) -> tuple[str, list[str]]:
        """
        Main entry point. Returns (text_to_speak, [action_names]).
        Thread-safe.
        """
        with self._lock:
            normalized = self._normalize_input(user_input)

            # Safety interrupt
            if self._is_interrupt(normalized):
                self._transition(State.IDLE)
                self._current_scope_rules = []
                self._unmatched_in_scope = 0
                print("[SAFETY] Interrupt received — returning to IDLE")
                return "OK. Stopping now.", []

            # Try matching
            speak, actions = self._do_match(normalized)

            if speak is None:
                return "I didn't understand that.", []

            return speak, actions

    def _do_match(self, normalized: str) -> tuple[str | None, list[str]]:
        """Try matching active rules. Updates state machine."""

        # If in scope, try subrules first
        if self._state == State.IN_SCOPE and self._current_scope_rules:
            for rule in self._current_scope_rules:
                matched, captured = self._match_rule(rule, normalized)
                if matched:
                    self.variables.update(captured)
                    self._unmatched_in_scope = 0
                    print(f"[MATCH] Subrule L{rule.level} line {rule.line_no}: ({rule.pattern})")
                    speak, actions = self._build_response(rule)

                    # Activate this rule's children if any
                    if rule.children:
                        new_depth = rule.level + 1
                        if new_depth > 6:
                            print(f"[ERROR] Max nesting depth exceeded (>6), resetting to IDLE.")
                            self._transition(State.IDLE)
                            self._current_scope_rules = []
                        else:
                            self._current_scope_rules = rule.children
                            self._transition(State.IN_SCOPE, new_depth)
                    else:
                        # Stay in current scope
                        pass

                    self._log_actions(actions)
                    return speak, actions

            # No subrule matched
            self._unmatched_in_scope += 1
            print(f"[SCOPE] No subrule matched ({self._unmatched_in_scope}/4 misses)")
            if self._unmatched_in_scope >= 4:
                print("[SCOPE] 4 consecutive misses — resetting to IDLE")
                self._transition(State.IDLE)
                self._current_scope_rules = []
                self._unmatched_in_scope = 0

        # Try top-level rules
        for rule in self.top_rules:
            matched, captured = self._match_rule(rule, normalized)
            if matched:
                self.variables.update(captured)
                self._unmatched_in_scope = 0
                print(f"[MATCH] Top-level rule L{rule.level} line {rule.line_no}: ({rule.pattern})")
                speak, actions = self._build_response(rule)

                # Activate children if any
                if rule.children:
                    self._current_scope_rules = rule.children
                    self._transition(State.IN_SCOPE, 1)
                else:
                    self._transition(State.IDLE)
                    self._current_scope_rules = []

                self._log_actions(actions)
                return speak, actions

        return None, []

    def _build_response(self, rule: Rule) -> tuple[str, list[str]]:
        speak, actions = _resolve_output(rule.output, self.definitions, self.variables, self.rng)
        # Filter unknown actions with warning
        valid_actions = []
        for a in actions:
            if a in self.KNOWN_ACTIONS:
                valid_actions.append(a)
            else:
                print(f"[WARN] Unknown action tag <{a}> ignored.")
        return speak, valid_actions

    def _log_actions(self, actions: list[str]):
        for a in actions:
            print(f"[ACTION] Queuing action: {a}")

    def reset(self):
        with self._lock:
            self._transition(State.IDLE)
            self._current_scope_rules = []
            self._unmatched_in_scope = 0
            self.variables.clear()
