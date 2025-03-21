# Copyright 2021 Fuzz Introspector Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
""" Utility functions """

import cxxfilt
import rust_demangler
import logging
import json
import os
import re
import shutil
import yaml
import pathlib

from bs4 import BeautifulSoup

from typing import Any, Optional

from fuzz_introspector import constants

logger = logging.getLogger(name=__name__)


def longest_common_prefix(strs: list[str]) -> str:
    """
    Dummy wrapper function for os.path.commonpath(paths: list[str]) -> str
    Keeping for backward compactibility
    """
    try:
        return os.path.commonpath(strs)
    except ValueError:
        return '/'


def normalise_str(s1: str) -> str:
    return s1.replace('\t', '').replace('\r', '').replace('\n',
                                                          '').replace(' ', '')


def safe_decode(data) -> Optional[str]:
    """Safe decode of binary data."""
    try:
        return data.decode()
    except Exception:
        pass
    try:
        return data.decode('unicode-escape')
    except Exception:
        pass

    return None


def get_all_files_in_tree_with_regex(basedir: str,
                                     regex_str: str) -> list[str]:
    """
    Returns a list of paths such that each path is to a file with
    the provided suffix. Walks the entire tree of basedir.
    """
    r = re.compile(regex_str)
    data_files = []
    for root, _, files in os.walk(basedir):
        for f in files:
            if r.match(f):
                data_files.append(os.path.join(root, f))
    return data_files


def data_file_read_yaml(filename: str) -> Optional[dict[Any, Any]]:
    """
    Reads a file as a yaml file. This is used to load data
    from fuzz-introspectors compiler plugin output.
    """
    if filename == '':
        return None
    if not os.path.isfile(filename):
        return None

    try:
        yaml.SafeLoader = yaml.CSafeLoader  # type: ignore[assignment, misc]
        logger.info('Set base loader to use CSafeLoader')
    except Exception:
        logger.info('Could not set CSafeLoader as base loader')

    try:
        with open(filename, 'r') as stream:
            data_dict: dict[Any, Any] = yaml.safe_load(stream)
            logger.info('Loaded single yaml module')
            return data_dict
    except Exception:
        # YAML library does not completely wrap exceptions, so unless
        # we catch all exceptions here we might end up in a crashing state.
        # This likely fails as the LLVM frontend now is putting multiple docs in
        # same yaml file. See commit 737ba72.
        pass

    # Try loading multiple yaml files in the fuzz introspector format
    # We need this because we have different formats for each language.
    logger.info('Trying to load multiple file formats together.')
    try:
        with open(filename, 'r') as yaml_f:
            data = yaml_f.read()
            docs = yaml.safe_load_all(data)
    except Exception as e:
        # YAML library does not completely wrap exceptions, so unless
        # we catch all exceptions here we might end up in a crashing state.
        logger.info('Failed loading YAML: %s', str(e))
        return None

    content = {}
    try:
        for doc in docs:
            if not doc or not isinstance(doc, dict):
                return None
            if 'Fuzzer filename' in doc and 'Fuzzer filename' not in content:
                content['Fuzzer filename'] = doc['Fuzzer filename']
            if 'All functions' in doc:
                if 'All functions' not in content:
                    content['All functions'] = doc['All functions']
                else:
                    content['All functions']['Elements'].extend(
                        doc['All functions']['Elements'])
    except Exception as e:
        # YAML library does not completely wrap exceptions, so unless
        # we catch all exceptions here we might end up in a crashing state.
        logger.info('Failed loading YAML: %s', str(e))
        return None

    if 'Fuzzer filename' not in content:
        return None
    if 'All functions' not in content:
        return None
    return content


def demangle_cpp_func(funcname: str) -> str:
    try:
        demangled: str = cxxfilt.demangle(funcname.replace(' ', ''))
        return demangled
    except Exception:
        return funcname


def demangle_rust_func(funcname: str) -> str:
    """Demangle the mangled rust function names."""
    # Ignore all non-mangled rust function names
    # All mangled rust function names started with _R
    if not funcname.startswith('_R'):
        return funcname

    try:
        demangled: str = rust_demangler.demangle(funcname.replace(' ', ''))
        demangled = demangled.replace('<', '').replace('>', '')
        return demangled
    except Exception:
        return funcname


def demangle_jvm_func(package: str, funcname: str) -> str:
    """Add package class name to uniquly identify jvm functons"""
    if funcname.startswith('['):
        return funcname

    return f'[{package}].{funcname}'


def remove_jvm_generics(funcname: str) -> str:
    """Remove generic arguments from the full jvm method name."""
    pattern = r'<[\s.,a-zA-Z0-9]+>|\\u003C[\s.,a-zA-Z0-9]+\\u003E'
    return re.sub(pattern, '', funcname)


def scan_executables_for_fuzz_introspector_logs(
        exec_dir: str) -> list[dict[str, str]]:
    """Finds all executables containing fuzzerLogFile string

    Args:
        exec_dir: Directory in which to search for executables.

    Returns:
        A list of dictionaries where each dictionary contains data about
        an executable that contains fuzzerLogFile string.
    """
    if not os.path.isdir(exec_dir):
        return []

    # Find all executables
    executable_files = []
    for f in os.listdir(exec_dir):
        full_path = os.path.join(exec_dir, f)
        if os.access(full_path, os.X_OK) and os.path.isfile(full_path):
            logger.info('File: %s is executable', full_path)
            executable_files.append(full_path)

    # Filter all executables containing "fuzzerLogFile" string
    executable_to_fuzz_reports = []
    text_pattern = re.compile('[A-Za-z0-9_-]{10,}')
    for executable_path in executable_files:
        with open(executable_path, 'rb') as fp:
            all_ascii_data = fp.read().decode('ascii', 'ignore')

        # Check if file contains fuzzerLogFile string
        for re_match in text_pattern.finditer(all_ascii_data):
            found_str = re_match.group(0)
            if 'fuzzerLogFile' not in found_str:
                continue
            logger.info('Found match %s', found_str)
            executable_to_fuzz_reports.append({
                'executable_path': executable_path,
                'fuzzer_log_file': found_str
            })
            # Break when a string is found to avoid scanning the whole binary.
            break

    return executable_to_fuzz_reports


def approximate_python_coverage_files_list(
        src1: str,
        possible_targets: list[tuple[str, str]],
        resolve_inits=False) -> Optional[str]:
    """Approximate python coverage file list from source."""
    # Remove prefixed .....
    src1 = src1.lstrip('.')

    # Generate list of potential candidates
    possible_candidates = []
    possible_init_candidates = []
    splits = src1.split('.')
    curr_str = ''
    for s2 in splits:
        curr_str = curr_str + s2
        possible_candidates.append(curr_str + '.py')
        possible_init_candidates.append(curr_str + '/__init__.py')
        curr_str = curr_str + '/'
    logger.debug('[%s] -- Created init candidates: %s', src1,
                 str(possible_init_candidates))

    # Start from backwards to find te longest possible candidate
    for candidate in reversed(possible_candidates):
        for fl, src2 in possible_targets:
            if src2.endswith(candidate):
                # Ensure the entire filename is matched in the event
                # of not slashes
                if '/' not in candidate and src2.split('/')[-1] != candidate:
                    continue
                logger.debug('Found target: %s', candidate)
                return fl

    # Will only get to hear if none of the above candidates matched. This
    # means the match is either in an __init__.py file or there is no match.
    if resolve_inits:
        for init_candidate in reversed(possible_init_candidates):
            for fl, src2 in possible_targets:
                if src2.endswith(init_candidate):
                    # Ensure the entire filename is matched in the event
                    # of not slashes
                    split = src2.split('/')[-1]
                    if '/' not in init_candidate and split != init_candidate:
                        continue
                    logger.debug('Found target: %s', init_candidate)
                    return fl
    logger.debug('Could not find target')
    return None


def get_target_coverage_url(coverage_url: str, target_name: str,
                            target_lang: str) -> str:
    """
    This function changes overall coverage URL to per-target coverage URL. Like:
        https://storage.googleapis.com/oss-fuzz-coverage/<project>/reports/<report-date>/linux
        to
        https://storage.googleapis.com/oss-fuzz-coverage/<project>/reports-by-target/<report-date>/<target-name>/linux
    """
    logger.info('Extracting coverage for %s -- %s', coverage_url, target_name)
    if os.environ.get('FUZZ_INTROSPECTOR'):
        if target_lang == 'c-cpp':
            return coverage_url.replace('reports',
                                        'reports-by-target').replace(
                                            '/linux', f'/{target_name}/linux')
        if target_lang == 'python':
            # TODO ADD python coverage link
            return coverage_url
        if target_lang == 'jvm':
            # TODO Add jvm coverage link
            return coverage_url
    # (TODO) This is temporary for local runs.
    return coverage_url


def load_func_names(input_list: list[str],
                    check_for_blocking: bool = True) -> list[str]:
    """
    Takes a list of function names (typically from llvm profile)
    and makes sure the output names are demangled.
    """
    loaded = []
    for reached in input_list:
        if (check_for_blocking
                and constants.BLOCKLISTED_FUNCTION_NAMES.match(reached)):
            continue
        loaded.append(demangle_rust_func(demangle_cpp_func(reached)))
    return loaded


def resolve_coverage_link(cov_url: str, source_file: str, lineno: int,
                          function_name: str, target_lang: str) -> str:
    """Resolves link to HTML coverage report for different languages"""
    result = '#'
    if target_lang in ['c-cpp', 'rust']:
        result = source_file + '.html#L' + str(lineno)
    elif target_lang == 'python':
        # Temporarily for debugging purposes. TODO: David remove this later
        # Find the html_status.json file. This is a file generated by the Python
        # coverate utility and contains mappings from source to html file. We
        # need this mapping in order to create links from the data extracted
        # during AST analysis, as there we only have the source code.
        html_summaries = get_all_files_in_tree_with_regex(
            '.', '.*html_status.json$')
        logger.debug(str(html_summaries))
        if len(html_summaries) > 0:
            html_idx = html_summaries[0]
            with open(html_idx, 'r') as jf:
                data = json.load(jf)
            possible_targets = []
            for fl in data['files']:
                possible_targets.append(
                    (fl, data['files'][fl]['index']['relative_filename']))

            found_target = approximate_python_coverage_files_list(
                function_name, possible_targets, True)
            if found_target is not None:
                result = found_target + '.html' + '#t' + str(lineno)
        else:
            logger.info('Could not find any html_status.json file')
    elif target_lang == 'jvm':
        # Retrieve class name for jvm function
        match = re.search(r'\[(.*?)\]\.', function_name)
        source_file = match.group(1) if match else source_file

        # Handle source class for jvm
        if '.' in source_file:
            # Source file has package, change package.class to package/class
            source_file = os.sep.join(source_file.rsplit('.', 1))
        else:
            # Source file has no package, add in default package
            source_file = os.path.join('default', source_file)

        # Handle subclass definition in the same source file
        source_file = source_file.split('$')[0]

        result = source_file + '.java.html#L' + str(lineno)
    elif target_lang == 'go':
        # Go coverage report only have a single page with no line index
        result = 'index.html'

        # Read the single coverage report html for processing
        report_html = ''
        report_path = os.path.join(os.environ.get('OUT', ''), 'report',
                                   'linux', 'index.html')
        if os.path.isfile(report_path):
            with open(report_path, 'r') as f:
                report_html = f.read()

        # Obtain the correct file value for the needed source code file
        if report_html:
            soup = BeautifulSoup(report_html, 'html.parser')
            select_element = soup.find('select', id='files')
            for option in select_element.find_all('option'):
                key = option['value']
                file = option.text.strip().split(' ')[0]
                if file.endswith(source_file):
                    result = f'{result}#{key}'
                    break
    else:
        logger.info('Unsupported language for coverage link resolve')

    if result != '#':
        result = cov_url.rstrip('/') + '/' + result.lstrip('/')

    return result


def group_path_list_by_target(
        path_list: list[list[Any]]) -> dict[Any, list[Any]]:
    """
    Group path list items by path target which is
    the last itme of each list.
    """
    result_dict: dict[Any, list[Any]] = {}
    for item in path_list:
        if len(item) == 0:
            continue
        if item[-1] in result_dict:
            item_list = result_dict[item[-1]]
        else:
            item_list = []

        item_list.append(item)
        result_dict[item[-1]] = item_list

    return result_dict


def check_coverage_link_existence(link: str) -> bool:
    link = link.split('#')[0]
    if link.startswith('/'):
        link = link[1:]
    return os.path.exists(link) and os.path.isfile(link)


def _find_all_source_path(extension: str) -> set[str]:
    """Search the $OUT/$SRC directory to find paths of all Java source files."""
    # Use set to avoid duplication
    source_path_list = set()

    # Retrieve $OUT and $SRC from environment variables
    out_dir = os.environ.get('OUT', None)
    src_dir = os.environ.get('SRC', None)
    logger.info('%s/%s', out_dir, src_dir)
    if out_dir and src_dir:
        # OSS-Fuzz store the source code in $OUT/$SRC directory
        path_to_search = os.path.join(out_dir, src_dir)
        if os.path.isdir(path_to_search):
            # Confirm that the source directory does exist
            for root, _, files in os.walk(path_to_search):
                if '/.' in root:
                    # Skipping hidden directory
                    continue
                for file in files:
                    if file.endswith(extension):
                        source_path_list.add(os.path.join(root, file))

    return source_path_list


def _copy_java_source_files(required_class_list: list[str], out_dir):
    """Copy the needed java source files."""
    logger.info('Copying java source files to %s',
                constants.SAVED_SOURCE_FOLDER)

    count = 0
    java_source_path_set = _find_all_source_path('.java')

    copied_source_path_list = []
    for required_class in set(required_class_list):
        # Remove inner class name
        required_file = required_class.split('$', 1)[0]

        # Transform class name to java source file name
        if not required_file.endswith('.java'):
            required_file = f'{required_file.replace(".", "/")}.java'

        for java_source_path in java_source_path_set:
            if java_source_path.endswith(required_file):
                # Source file for the target class found. Copy it to the
                # SAVED_SOURCE_FOLDER while preserving package directories
                # of the target source file.
                dst = os.path.join(out_dir, constants.SAVED_SOURCE_FOLDER,
                                   required_file)
                if os.path.isfile(dst):
                    # Skip duplicate files
                    continue
                os.makedirs(os.path.join(out_dir, os.path.dirname(dst)),
                            exist_ok=True)
                shutil.copy(java_source_path, dst)
                count += 1
                copied_source_path_list.append(required_file)
                break

    if not count:
        logger.info('No java source files copied.')
        return

    # Store a list of existing source file paths for reference
    with open(
            os.path.join(out_dir, constants.SAVED_SOURCE_FOLDER, 'index.json'),
            'w') as f:
        f.write(json.dumps(copied_source_path_list))

    logger.info('Copied %d java source files to %s', count,
                constants.SAVED_SOURCE_FOLDER)


def _copy_python_source_files(out_dir):
    """Copy the needed python source files."""
    logger.info('Copying python source files to %s',
                constants.SAVED_SOURCE_FOLDER)

    count = 0
    python_source_path_set = _find_all_source_path('.py')
    os.makedirs(os.path.join(out_dir, constants.SAVED_SOURCE_FOLDER),
                exist_ok=True)

    copied_source_path_list = []
    for python_source_path in python_source_path_set:
        filename = os.path.basename(python_source_path)
        dst = os.path.join(out_dir, constants.SAVED_SOURCE_FOLDER, filename)

        if os.path.isfile(dst):
            # Skip duplicate files
            continue

        shutil.copy(python_source_path, dst)
        count += 1
        copied_source_path_list.append(filename)

    # Store a list of existing source file paths for reference
    with open(
            os.path.join(out_dir, constants.SAVED_SOURCE_FOLDER, 'index.json'),
            'w') as f:
        f.write(json.dumps(copied_source_path_list))

    logger.info('Copied %d python source files to %s', count,
                constants.SAVED_SOURCE_FOLDER)


def copy_source_files(required_class_list: list[str],
                      language: str,
                      out_dir: str = ''):
    """Copy the needed source files for different project.
    Currently only support Python and Java projects."""

    if language == 'jvm':
        _copy_java_source_files(required_class_list, out_dir)
    elif language == 'python':
        _copy_python_source_files(out_dir)
    else:
        logger.debug('Language: %s not support. Skipping source file copy.',
                     language)


def locate_rust_fuzz_key(funcname: str, fuzz_map: dict[str,
                                                       Any]) -> Optional[str]:
    """Helper method for locating rust fuzz key with missing crate
    information."""

    while funcname:
        match = next((key for key in fuzz_map if key.endswith(funcname)), None)
        # Ensure the matched key contains crate information which is
        # unique for rust
        if match and '::' in match:
            return match

        if '::' in funcname:
            funcname = funcname.split('::', 1)[1]
        else:
            break

    return None


def locate_rust_fuzz_item(funcname: str, item_list: list[str]) -> str:
    """Helper method for locating str item with missing crate information."""

    if funcname in item_list:
        return funcname

    while funcname:
        for item in item_list:
            if item.endswith(funcname) and '::' in item:
                return item

        if '::' in funcname:
            funcname = funcname.split('::', 1)[1]
        else:
            break

    return ''


def detect_language(directory) -> str:
    """Given a folder finds the likely programming language of the project"""

    paths_to_avoid = [
        '/src/aflplusplus', '/src/honggfuzz', '/src/libfuzzer', '/src/fuzztest'
    ]

    language_counts: dict[str, int] = {}
    for dirpath, _, filenames in os.walk(directory):
        if any([x for x in paths_to_avoid if dirpath.startswith(x)]):
            continue
        for filename in filenames:
            # pylint: disable-next=no-member
            for language, extensions in constants.LANGUAGE_EXTENSIONS.items():
                if pathlib.Path(filename).suffix in extensions:
                    curr_count = language_counts.get(language, 0)
                    language_counts[language] = curr_count + 1

    max_lang = ''
    max_count = -1
    for language, count in language_counts.items():
        if count >= max_count:
            max_count = count
            max_lang = language
    return max_lang
