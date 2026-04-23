"""
dialog_engine.py
Parses a TangoChat-style DSL script and performs rule matching.
Produces (speak_text, [action_tags], interrupted) for each user input.
"""

import re
import random
import threading
from enum import Enum, auto


class State(Enum):
    BOOT = auto()
    IDLE = auto()
    IN_SCOPE = auto()
    EXEC_ACTIONS = auto()


class Rule:
    def __init__(self, level: int, pattern: str, output: str, children=None, line_no: int = 0):
        self.level = level
        self.pattern = pattern
        self.output = output
        self.children = children or []
        self.line_no = line_no


def _parse_bracket_options(text: str) -> list[str]:
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
        elif ch.isspace() and not in_quote:
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
    def replacer(m):
        name = m.group(1)
        if name in definitions:
            opts = definitions[name]
            return "[" + " ".join(f'"{o}"' if " " in o else o for o in opts) + "]"
        return m.group(0)

    return re.sub(r'~(\w+)', replacer, text)


def _pattern_to_regex(pattern: str, definitions: dict) -> re.Pattern:
    """
    Convert a DSL pattern to regex.
    Supports:
    - plain text
    - [choices]
    - "quoted phrases"
    - _ wildcard capture
    - ~definitions
    """
    expanded = _expand_definitions(pattern, definitions)

    result = ""
    i = 0

    while i < len(expanded):
        ch = expanded[i]

        if ch == '[':
            try:
                end = expanded.index(']', i)
            except ValueError:
                raise ValueError(f"Unbalanced [ ] in pattern: {pattern}")

            inner = expanded[i + 1:end]
            options = _parse_bracket_options(inner)
            escaped = [re.escape(o).replace(r'\ ', r'\s+') for o in options]
            result += r"(?:%s)" % "|".join(escaped)
            i = end + 1

        elif ch == '_':
            result += r"(.+?)"
            i += 1

        elif ch == '"':
            try:
                end = expanded.index('"', i + 1)
            except ValueError:
                raise ValueError(f'Unbalanced quotes in pattern: {pattern}')
            phrase = expanded[i + 1:end]
            result += re.escape(phrase).replace(r'\ ', r'\s+')
            i = end + 1

        elif ch.isspace():
            while i < len(expanded) and expanded[i].isspace():
                i += 1
            result += r"\s+"

        else:
            result += re.escape(ch)
            i += 1

    full_pattern = r"(?:.*\s)?%s(?:\s.*)?" % result.strip()
    return re.compile(full_pattern, re.IGNORECASE)


def _resolve_output(output: str, definitions: dict, variables: dict, rng: random.Random) -> tuple[str, list[str]]:
    actions = re.findall(r'<(\w+)>', output)
    text = re.sub(r'<\w+>', '', output).strip()

    def expand_def(m):
        name = m.group(1)
        if name in definitions:
            return rng.choice(definitions[name])
        return m.group(0)

    text = re.sub(r'~(\w+)', expand_def, text)

    def resolve_bracket(m):
        inner = m.group(1)
        options = _parse_bracket_options(inner)
        if options:
            return rng.choice(options)
        return ""

    text = re.sub(r'\[([^\]]*)\]', resolve_bracket, text)

    def resolve_var(m):
        var_name = m.group(1)
        return variables.get(var_name, "I don't know")

    text = re.sub(r'\$(\w+)', resolve_var, text)
    text = re.sub(r'\s+', ' ', text).strip()

    return text, actions


class DialogEngine:
    KNOWN_ACTIONS = {"head_yes", "head_no", "arm_raise", "dance90"}

    def __init__(self, script_path: str, seed: int = None):
        self.script_path = script_path
        self.rng = random.Random(seed)

        self.definitions: dict[str, list[str]] = {}
        self.top_rules: list[Rule] = []
        self.variables: dict[str, str] = {}

        self._state = State.BOOT
        self._current_scope_rules: list[Rule] = []
        self._scope_depth = 0
        self._unmatched_in_scope = 0
        self._lock = threading.Lock()

        self._resume_state = State.IDLE
        self._resume_scope_depth = 0

        self._parse(script_path)
        self._transition(State.IDLE)
        print(
            f"[DialogEngine] Loaded {len(self.top_rules)} top-level rules, "
            f"{len(self.definitions)} definitions."
        )

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

    def begin_action_execution(self):
        with self._lock:
            if self._state == State.EXEC_ACTIONS:
                return
            self._resume_state = self._state
            self._resume_scope_depth = self._scope_depth
            self._transition(State.EXEC_ACTIONS)

    def end_action_execution(self):
        with self._lock:
            if self._state != State.EXEC_ACTIONS:
                return
            if self._resume_state == State.IN_SCOPE:
                self._transition(State.IN_SCOPE, self._resume_scope_depth)
            else:
                self._transition(self._resume_state)

    def _parse(self, path: str):
        try:
            with open(path, "r") as f:
                raw_lines = f.readlines()
        except FileNotFoundError:
            print(f"[FATAL] Script file not found: {path}")
            raise

        cleaned_lines = []
        for line_no, raw in enumerate(raw_lines, start=1):
            line = raw.rstrip("\n")
            comment_pos = line.find("#")
            if comment_pos >= 0:
                line = line[:comment_pos]
            if line.strip():
                cleaned_lines.append((line_no, line.rstrip()))

        # Pass 1: definitions
        non_definition_lines = []
        for line_no, content in cleaned_lines:
            stripped = content.strip()
            def_match = re.match(r'^~(\w+)\s*:\s*\[(.+)\]$', stripped)
            if def_match:
                name = def_match.group(1)
                options = _parse_bracket_options(def_match.group(2))
                if not options:
                    print(f"[WARN] {path}:{line_no}: NON-FATAL: Empty definition ~{name}, skipping.")
                    continue
                self.definitions[name] = options
                print(f"[PARSE] Definition ~{name} = {options}")
            elif stripped.startswith("~"):
                print(f"[ERROR] {path}:{line_no}: NON-FATAL: Bad definition syntax, skipping: {stripped}")
            else:
                non_definition_lines.append((line_no, content))

        # Pass 2: rules
        rule_stack: list[Rule] = []
        top_rules: list[Rule] = []

        for line_no, content in non_definition_lines:
            stripped = content.strip()

            rule_match = re.match(r'^(u\d*)\s*:\s*\(([^)]*)\)\s*:\s*(.*)$', stripped)
            if not rule_match:
                bad_match = re.match(r'^(u\d*)\s*:\s*\(([^)]*)\)\s+(\S.*)$', stripped)
                if bad_match:
                    print(f"[ERROR] {path}:{line_no}: NON-FATAL: Missing ':' after pattern, skipping: {stripped}")
                elif re.match(r'^u\d*\s*:', stripped):
                    print(f"[ERROR] {path}:{line_no}: NON-FATAL: Malformed rule, skipping: {stripped}")
                continue

            level_str = rule_match.group(1)
            pattern = rule_match.group(2).strip()
            output = rule_match.group(3).strip()

            if level_str == "u":
                level = 0
            else:
                level = int(level_str[1:])

            if pattern.count("[") != pattern.count("]"):
                print(f"[ERROR] {path}:{line_no}: NON-FATAL: Unbalanced brackets in pattern, skipping: {stripped}")
                continue

            if output.count("[") != output.count("]"):
                print(f"[ERROR] {path}:{line_no}: NON-FATAL: Unbalanced brackets in output, skipping: {stripped}")
                continue

            tags_in_output = re.findall(r'<(\w+)>', output)
            for tag in tags_in_output:
                if tag not in self.KNOWN_ACTIONS:
                    print(f"[WARN] {path}:{line_no}: Unknown action tag <{tag}>, will be ignored at runtime.")

            rule = Rule(level=level, pattern=pattern, output=output, line_no=line_no)
            print(f"[PARSE] Rule L{level} line {line_no}: ({pattern}) -> {output[:50]}...")

            if level == 0:
                top_rules.append(rule)
                rule_stack = [rule]
            else:
                while rule_stack and rule_stack[-1].level >= level:
                    rule_stack.pop()

                if not rule_stack:
                    print(f"[ERROR] {path}:{line_no}: NON-FATAL: Subrule u{level} has no parent, skipping.")
                    continue

                parent = rule_stack[-1]
                parent.children.append(rule)
                rule_stack.append(rule)

        self.top_rules = top_rules

        if not self.top_rules:
            print(f"[FATAL] {path}: No valid top-level u: rules found. Refusing to run.")
            raise ValueError("No valid top-level rules in script.")

    def _normalize_input(self, text: str) -> str:
        text = text.lower()
        text = re.sub(r'[.,!?]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _match_rule(self, rule: Rule, user_input: str) -> tuple[bool, dict]:
        try:
            regex = _pattern_to_regex(rule.pattern.strip(), self.definitions)
        except ValueError as e:
            print(f"[ERROR] {self.script_path}:{rule.line_no}: NON-FATAL: {e}")
            return False, {}

        m = regex.fullmatch(user_input)
        if not m:
            return False, {}

        captures = m.groups()
        captured = {}

        if captures:
            var_names = re.findall(r'\$(\w+)', rule.output)
            for i, val in enumerate(captures):
                if i < len(var_names):
                    captured[var_names[i]] = val.strip()

        return True, captured

    def _is_interrupt(self, user_input: str) -> bool:
        words = user_input.lower().split()
        return any(w in {"stop", "cancel", "reset", "quit"} for w in words)

    def process_input(self, user_input: str) -> tuple[str, list[str], bool]:
        with self._lock:
            normalized = self._normalize_input(user_input)

            if self._is_interrupt(normalized):
                print("[SAFETY] Interrupt received — returning to IDLE")
                self._current_scope_rules = []
                self._unmatched_in_scope = 0
                self._transition(State.IDLE)
                return "OK. Stopping now.", [], True

            speak, actions = self._do_match(normalized)

            if speak is None:
                return "I didn't understand that.", [], False

            return speak, actions, False

    def _do_match(self, normalized: str) -> tuple[str | None, list[str]]:
        if self._state == State.IN_SCOPE and self._current_scope_rules:
            for rule in self._current_scope_rules:
                matched, captured = self._match_rule(rule, normalized)
                if matched:
                    self.variables.update(captured)
                    self._unmatched_in_scope = 0
                    print(f"[MATCH] Subrule L{rule.level} line {rule.line_no}: ({rule.pattern})")

                    speak, actions = self._build_response(rule)

                    if rule.children:
                        new_depth = rule.level + 1
                        if new_depth > 6:
                            print("[ERROR] Max nesting depth exceeded (>6), resetting to IDLE.")
                            self._current_scope_rules = []
                            self._unmatched_in_scope = 0
                            self._transition(State.IDLE)
                        else:
                            self._current_scope_rules = rule.children
                            self._transition(State.IN_SCOPE, new_depth)

                    self._log_actions(actions)
                    return speak, actions

            self._unmatched_in_scope += 1
            print(f"[SCOPE] No subrule matched ({self._unmatched_in_scope}/4 misses)")
            if self._unmatched_in_scope >= 4:
                print("[SCOPE] 4 consecutive misses — resetting to IDLE")
                self._current_scope_rules = []
                self._unmatched_in_scope = 0
                self._transition(State.IDLE)

        for rule in self.top_rules:
            matched, captured = self._match_rule(rule, normalized)
            if matched:
                self.variables.update(captured)
                self._unmatched_in_scope = 0
                print(f"[MATCH] Top-level rule L{rule.level} line {rule.line_no}: ({rule.pattern})")

                speak, actions = self._build_response(rule)

                if rule.children:
                    self._current_scope_rules = rule.children
                    self._transition(State.IN_SCOPE, 1)
                else:
                    self._current_scope_rules = []
                    self._transition(State.IDLE)

                self._log_actions(actions)
                return speak, actions

        return None, []

    def _build_response(self, rule: Rule) -> tuple[str, list[str]]:
        speak, actions = _resolve_output(rule.output, self.definitions, self.variables, self.rng)

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

    def reset(self, clear_variables: bool = True):
        with self._lock:
            self._current_scope_rules = []
            self._unmatched_in_scope = 0
            self._resume_state = State.IDLE
            self._resume_scope_depth = 0
            if clear_variables:
                self.variables.clear()
            self._transition(State.IDLE)
