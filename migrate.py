#!/usr/bin/env python
# PYTHON_ARGCOMPLETE_OK
"""A script for migrating Ansible repositories from Shippable to Azure Pipelines."""

import argparse
import dataclasses
import os
import re
import shutil
import sys
import typing as t

import ruamel.yaml

try:
    import argcomplete
except ImportError:
    argcomplete = None

"""
Mapping of Shippable test script names to a tuple containing a stage and job name.
"""
test_types = dict(
    sanity=('Sanity', 'Test'),
    units=('Units', 'Python'),
    windows=('Windows', 'Server'),
    osx=('Remote', 'OS X'),
    macos=('Remote', 'macOS'),
    rhel=('Remote', 'RHEL'),
    freebsd=('Remote', 'FreeBSD'),
    linux=('Docker', ''),
    fallaxy=('Fallaxy', 'Python'),
    galaxy=('Galaxy', 'Python'),
    generic=('Generic', 'Python'),
    network=('Network', 'Python'),
    aws=('AWS', 'Python'),
    vcenter=('vCenter', 'Python'),
    cs=('CloudStack', 'Python'),
    tower=('Tower', 'Python'),
    cloud=('Cloud', 'Python'),
    hcloud=('Hetzner', 'Python'),
    ios=('IOS', 'Python'),
    vyos=('VyOS', 'Python'),
    azure=('Azure', 'Python'),
)

"""
Mapping of ansible-test docker container short-names to a job name and version number.
"""
docker_types = dict(
    alpine3=('Alpine', '3'),
    centos6=('CentOS', '6'),
    centos7=('CentOS', '7'),
    centos8=('CentOS', '8'),
    fedora29=('Fedora', '29'),
    fedora30=('Fedora', '30'),
    fedora31=('Fedora', '31'),
    fedora32=('Fedora', '32'),
    ubuntu1604=('Ubuntu', '16.04'),
    ubuntu1804=('Ubuntu', '18.04'),
    opensuse15=('openSUSE', '15'),
    opensuse15py2=('openSUSE', '15 py2'),
)

"""
Tuple of job names (defined in the mappings above) that will be given their own stage if they exist as incidental tests.
All other incidental tests will be combined into a single incidental stage.
"""
split_incidental = (
    'Docker',
    'Remote',
    'Windows',
)


@dataclasses.dataclass
class TestConfig:
    """
    Parsed configuration representing a single test entry in the Shippable test matrix.
    Includes properties used to generate the desired naming and structure for use in Azure Pipelines.
    """
    stage_label: str
    job_label: str
    type: str
    platform: t.Optional[str]
    version: t.Optional[str]
    group: t.Optional[str]
    incidental: bool
    branch_prefix: t.Optional[str]
    branch_kvp: t.Optional[str]

    @property
    def stage_name(self) -> str:
        """The name of the Azure Pipelines stage to place this test into."""
        stage_name = self.stage_label

        if self.branch_name:
            stage_name += f' {self.branch_name}'

        if self.incidental:
            if self.stage_label in split_incidental:
                stage_name = f'Incidental {stage_name}'
            else:
                stage_name = 'Incidental'

        return stage_name

    @property
    def branch_name(self) -> t.Optional[str]:
        """The Ansible branch name to display in Azure Pipelines."""
        if self.branch_prefix:
            branch = self.branch_prefix
        elif self.branch_kvp:
            branch = self.branch_kvp[1]
        else:
            branch = None

        if branch:
            branch = branch.replace('stable-', '')

        return branch

    @property
    def name_components(self) -> t.Tuple[str, ...]:
        """A tuple of components that make up the name of the job which is displayed in Azure Pipelines."""
        parts = [self.job_label]

        if self.incidental and self.stage_label not in split_incidental:
            parts.insert(0, self.stage_label)

        if self.version:
            if self.type == 'linux' and self.job_label == 'openSUSE' and self.version == '15':
                parts.append('15 py3')
            else:
                parts.append(self.version)

        return tuple(parts)

    @property
    def test_components(self) -> t.Tuple[str, ...]:
        """A tuple of components that make up the test identifier which is passed to the shell scripts for execution."""
        if self.type == 'linux':
            parts = [self.type, self.platform + self.version.replace(' ', '').replace('.', '')]
        else:
            parts = [self.type, self.platform, self.version]

        parts = [part for part in parts if part is not None]

        if self.incidental:
            parts.insert(0, 'i')

        if self.branch_prefix:
            parts.insert(0, self.branch_prefix)

        return tuple(parts)

    @property
    def test(self) -> str:
        """
        A reconstructed version of the original test identifier used on Shippable.
        Used for verification purposes to ensure the generated matrix matches the original.
        """
        parts = list(self.test_components)

        if self.group:
            parts.append(self.group)

        test = '/'.join(parts)

        return test


@dataclasses.dataclass
class Target:
    name: str
    type: str


@dataclasses.dataclass
class Stage:
    name: str
    incidental: bool
    targets: t.Dict[str, Target] = dataclasses.field(default_factory=dict)
    configs: t.List[TestConfig] = dataclasses.field(default_factory=list)
    groups: t.Set[str] = dataclasses.field(default_factory=set)

    @property
    def target_count(self):
        return len(self.targets)

    @property
    def group_count(self):
        return len(self.groups) or 1

    @property
    def job_count(self):
        return self.target_count * self.group_count


@dataclasses.dataclass
class MatrixItem:
    raw: str
    test: str
    values: t.Dict[str, str]
    parts: t.Tuple[str, ...]


def parse_shippable_matrix(path: str) -> t.List[MatrixItem]:
    """Return a list of tuples representing matrix entries parsed from the given Shippable YAML."""
    yaml = ruamel.yaml.YAML()

    with open(path) as file:
        shippable = yaml.load(file)

    matrix_include = shippable['matrix']['include']

    matrix = []

    for item in matrix_include:
        raw = item['env']
        values = dict(kvp.split('=') for kvp in raw.split(' '))
        test = values.pop('T')
        parts = test.split('/')
        matrix.append(MatrixItem(raw=raw, values=values, parts=parts, test=test))

    return matrix


def get_test_config(test_type: str, parts: t.Tuple[str, ...], incidental: bool, branch_prefix: t.Optional[str], branch_kvp: t.Optional[str]) -> TestConfig:
    labels = test_types.get(test_type)

    if not labels:
        raise Exception(f'Unknown test type "{test_type}" extracted from test parts: {parts}')

    stage_label, job_label = labels

    platform = None
    version = None
    group = None

    if len(parts) == 0:
        pass
    elif len(parts) == 1:
        if job_label == 'Python':
            if '.' in parts[0]:
                version, = parts
            else:
                group, = parts
        else:
            version, = parts
    elif len(parts) == 2:
        version, group = parts
    elif len(parts) == 3:
        platform, version, group = parts
    else:
        raise Exception(f'Unhandled test type "{test_type}" with test parameters: {"/".join(parts)}')

    if test_type == 'linux':
        if platform or not version:
            raise Exception(f'Unexpected test parameters: {"/".join(parts)}')

        docker_type = docker_types.get(version)

        if not docker_type:
            raise Exception(f'Unexpected docker container reference "{version}" with test parameters: {"/".join(parts)}')

        job_label = docker_type[0]
        platform = docker_type[0].lower()
        version = docker_type[1]

    test_config = TestConfig(
        stage_label=stage_label,
        job_label=job_label,
        type=test_type,
        platform=platform,
        version=version,
        group=group,
        incidental=incidental,
        branch_prefix=branch_prefix,
        branch_kvp=branch_kvp,
    )

    return test_config


def classify_matrix_item(path: str, is_collection: bool, matrix_item: MatrixItem) -> TestConfig:
    ansible_branches = (
        'devel',
        'stable-2.10',  # only used by *.aws with A_REV
        'stable-2.9',  # only used by *.aws with A_REV
        '2.10',
        '2.9',
    )

    parts = matrix_item.parts
    values = dict(matrix_item.values)

    # Some collections run tests against specific Ansible branches.
    # Extract this information from the matrix item before continuing.

    branch_prefix = None
    branch_kvp = None

    if parts[0] in ansible_branches:
        branch_prefix = parts[0]
        parts = parts[1:]
    else:
        for key, value in list(values.items()):
            if value in ansible_branches:
                branch_kvp = (key, value)  # A_REV used by *.aws
                values.pop(key)
                break

    # Check for any unused key value pairs.
    # If there are any, then there's something about the matrix definition this tool doesn't understand.

    if values:
        raise Exception(f'Unrecognized matrix key/value pairs detected: {values}')

    # Check to see if the test is an incidental test.

    incidental = False

    if parts[0] == 'i':
        incidental = True
        parts = parts[1:]

    # Determine the script which tests are delegated to.
    # This should always be the first part of the matrix entry.

    test_type = parts[0]
    parts = parts[1:]

    test_config = get_test_config(test_type, parts, incidental, branch_prefix, branch_kvp)

    if matrix_item.test != test_config.test:
        raise Exception(f'The post-processed test entry "{test_config.test}" does not match the original "{matrix_item.test}".')

    # Verify the script associated with the test actually exists.

    if is_collection:
        script_directory = os.path.join(path, f'tests/utils/shippable')
    else:
        script_directory = os.path.join(path, f'test/utils/shippable')

    if incidental:
        script_directory = os.path.join(script_directory, 'incidental')

    script_path = os.path.join(script_directory, f'{test_type}.sh')

    if not os.path.exists(script_path):
        raise Exception(f'Detected test type "{test_type}" does not have matching script: {script_path}')

    return test_config


def main() -> None:
    """Main program entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument('working_tree', help='path to the working tree to migrate')

    if argcomplete:
        argcomplete.autocomplete(parser)

    args = parser.parse_args()

    if sys.version_info < (3, 8):
        raise Exception(f'Python 3.8+ is required, but Python {".".join(str(i) for i in sys.version_info[:2])} is being used.')

    input_directory = args.working_tree
    content_directory = os.path.join(os.path.dirname(__file__), 'content')
    output_directory = os.path.join(input_directory, '.azure-pipelines')
    output_filename = os.path.join(output_directory, 'azure-pipelines.yml')
    galaxy_filename = os.path.join(input_directory, 'galaxy.yml')

    yaml = ruamel.yaml.YAML()

    try:
        with open(galaxy_filename) as input_file:
            galaxy = yaml.load(input_file)
            is_collection = True
    except FileNotFoundError:
        galaxy = None
        is_collection = False

    if galaxy:
        checkout_path = os.path.join('ansible_collections', galaxy['namespace'], galaxy['name'])
        main_branch = 'main'  # best guess, not always correct

        branches = [
            main_branch,
            'stable-*',
        ]
    else:
        checkout_path = 'ansible'
        main_branch = 'devel'

        branches = [
            main_branch,
            'stable-*',
        ]

    parsed_matrix = parse_shippable_matrix(os.path.join(input_directory, 'shippable.yml'))
    classified_matrix = [classify_matrix_item(input_directory, is_collection, item) for item in parsed_matrix]

    content_stages = generate_stages(classified_matrix)

    content = generate_pipelines_config(content_stages, branches, checkout_path, main_branch, is_collection)

    write_content(content, content_directory, input_directory, output_directory, output_filename, is_collection)


def generate_stages(classified_matrix) -> t.List[t.Dict[str, t.Any]]:
    stages = {}

    for item in classified_matrix:
        stage = stages.setdefault(item.stage_name, Stage(item.stage_name, item.incidental))

        if stage.incidental != item.incidental:
            raise Exception(f'Target "{item.stage_name}" has a test mismatch between "{stage.incidental}" and "{item.incidental}".')

        stage.configs.append(item)

        target_name = ' '.join(item.name_components)
        test_type = '/'.join(item.test_components)

        target = stage.targets.setdefault(target_name, Target(target_name, test_type))

        if target.type != test_type:
            raise Exception(f'Target "{target_name}" has a test mismatch between "{target.type}" and "{test_type}".')

        if item.group:
            stage.groups.add(item.group)

    converted_jobs = sum(stage.job_count for stage in stages.values())

    print(f'Converted {converted_jobs} jobs (entries * groups = jobs):')

    for stage_name, stage in stages.items():
        print(f'  {stage_name}: {stage.target_count} * {stage.group_count} = {stage.job_count}')

    if converted_jobs < len(classified_matrix):
        raise Exception(f'Found {len(classified_matrix)} jobs but only converted {converted_jobs}.')

    if converted_jobs > len(classified_matrix):
        print(f'WARNING: The resulting matrix contains {converted_jobs} jobs instead of the original {len(classified_matrix)} jobs.', file=sys.stderr)

    # Generate content.

    content_stages = []

    for stage_name, stage in stages.items():
        test_prefix = tuple()
        test_suffix = tuple()
        name_prefix = tuple()
        groups = None

        for i in range(1, 10):
            prefix = set(config.test_components[0:i] for config in stage.configs)

            if len(prefix) != 1:
                break

            prefix = list(prefix)[0]

            if not prefix[-1]:
                break

            test_prefix = prefix

        for i in range(1, 10):
            prefix = set(config.name_components[0:i] for config in stage.configs)

            if len(prefix) != 1:
                break

            prefix = list(prefix)[0]

            if not prefix[-1]:
                break

            name_prefix = prefix

        if stage.groups:
            if len(stage.groups) == 1:
                test_suffix = tuple(list(stage.groups)[0], )
            else:
                groups = clean_values(stage.groups)

        test_format = '/'.join(test_prefix + ('{0}',) + test_suffix)

        if test_prefix:
            test_offset = len('/'.join(test_prefix) + '/')
        else:
            test_offset = 0

        name_format = ' '.join(name_prefix + ('{0}',))

        if name_prefix:
            name_offset = len(' '.join(name_prefix) + ' ')
        else:
            name_offset = 0

        targets = []

        for target in stage.targets.values():
            target_add = dict(
                name=clean_value(target.name[name_offset:]),
                test=clean_value(target.type[test_offset:]),
            )

            if target_add['name'] == target_add['test']:
                del target_add['name']

            targets.append(target_add)

        content_stage = dict(
            stage=stage.name.replace(' ', '_').replace(".", "_"),
            displayName=stage.name,
            dependsOn=[],
            jobs=[
                dict(
                    template='templates/matrix.yml',
                    parameters=dict(
                        nameFormat=name_format,
                        testFormat=test_format,
                        targets=targets,
                        groups=groups,
                    ),
                ),
            ],
        )

        if name_format == '{0}':
            del content_stage['jobs'][0]['parameters']['nameFormat']

        if test_format == '{0}':
            del content_stage['jobs'][0]['parameters']['testFormat']

        if not groups:
            del content_stage['jobs'][0]['parameters']['groups']

        if content_stage['displayName'] == content_stage['stage']:
            del content_stage['displayName']

        content_stages.append(content_stage)

    stage_names = [item['stage'] for item in content_stages]

    summary_stage = dict(
        stage='Summary',
        condition='succeededOrFailed()',
        dependsOn=stage_names,
        jobs=[
            dict(
                template='templates/coverage.yml',
            ),
        ],
    )

    content_stages.append(summary_stage)

    return content_stages


def generate_pipelines_config(
        content_stages: t.List[t.Dict[str, t.Any]],
        branches: t.List[str],
        checkout_path: str,
        main_branch: str,
        is_collection: bool,
) -> t.Dict[str, t.Any]:
    """Generate an Azure Pipelines configuration file."""
    if is_collection:
        entry_point = 'tests/utils/shippable/shippable.sh'
    else:
        entry_point = 'test/utils/shippable/shippable.sh'

    content = dict(
        trigger=dict(
            batch=True,
            branches=dict(
                include=list(branches),
            ),
        ),
        pr=dict(
            autoCancel=True,
            branches=dict(
                include=list(branches),
            ),
        ),
        schedules=[
            dict(
                cron='0 0 * * *',
                displayName='Nightly',
                always=True,
                branches=dict(
                    include=list(branches),
                ),
            ),
        ],
        variables=[
            dict(
                name='checkoutPath',
                value=checkout_path,
            ),
            dict(
                name='coverageBranches',
                value=main_branch,
            ),
            dict(
                name='pipelinesCoverage',
                value='coverage',
            ),
            dict(
                name='entryPoint',
                value=entry_point,
            ),
            dict(
                name='fetchDepth',
                value=100,
            ),
        ],
        resources=dict(
            containers=[
                dict(
                    container='default',
                    image='quay.io/ansible/azure-pipelines-test-container:1.6.0',
                ),
            ],
        ),
        pool='Standard',
        stages=content_stages,
    )

    return content


def write_content(
        content: t.Dict[str, t.Any],
        content_directory: str,
        input_directory: str,
        output_directory: str,
        output_filename: str,
        is_collection: bool,
) -> None:
    """Write the Azure Pipelines config and apply patches to existing scripts."""
    shutil.copytree(content_directory, output_directory, dirs_exist_ok=True)

    yaml = ruamel.yaml.YAML()
    yaml.indent(sequence=4, offset=2)

    with open(output_filename, 'w') as output_file:
        yaml.dump(content, output_file, transform=yaml_transformer)

    patch_scripts(input_directory, is_collection)


def clean_values(values: t.List[str]) -> t.List[t.Union[int, float, str]]:
    """Return the given list with each value converted to an int or float if possible, otherwise as the original string."""
    return [clean_value(value) for value in sorted(values)]


def clean_value(value: str) -> t.Union[int, float, str]:
    """Return the given value as an int or float if possible, otherwise as the original string."""
    try:
        return int(value)
    except ValueError:
        pass

    try:
        return float(value)
    except ValueError:
        pass

    return value


def patch_scripts(work_tree: str, is_collection: bool) -> None:
    """Applies minimal patches to existing shell scripts to correct known compatibility issues with Shippable scripts running on Azure Pipelines."""
    if is_collection:
        shippable_sh_path = os.path.join(work_tree, 'tests/utils/shippable/shippable.sh')
    else:
        shippable_sh_path = os.path.join(work_tree, 'test/utils/shippable/shippable.sh')

    with open(shippable_sh_path) as file:
        lines = file.read().splitlines()

    output = []

    for line in lines:
        if re.search(r'^trap cleanup', line):
            # the cleanup functions executed on Shippable were for code coverage, which is handled differently on Azure Pipelines
            line = f'if [ "${{SHIPPABLE_BUILD_ID:-}}" ]; then {line}; fi'

        if re.search(r'/check_matrix\.py"?$', line):
            # the matrix checking script only works on Shippable
            line = f'if [ "${{SHIPPABLE_BUILD_ID:-}}" ]; then {line}; fi'

        # make sure cleanup of running containers does not terminate the azure-pipelines-test-container used to run test jobs

        common = "for container in $(docker ps --format '{{.Image}} {{.ID}}' | grep -v "

        if line == common + ''''^drydock/' | sed 's/^.* //'); do''':
            line = common + '''-e '^drydock/' -e '^quay.io/ansible/azure-pipelines-test-container:' | sed 's/^.* //'); do'''

        if line == common + '''-e '^drydock/' -e '^quay.io/ansible/shippable-build-container:' | sed 's/^.* //'); do''':
            line = common + '''-e '^drydock/' -e '^quay.io/ansible/shippable-build-container:' -e '^quay.io/ansible/azure-pipelines-test-container:' | sed 's/^.* //'); do'''

        output.append(line)

    with open(shippable_sh_path, 'w') as file:
        file.write('\n'.join(output) + '\n')


def yaml_transformer(value: str) -> str:
    """A YAML transformer for Ruamel that places a blank line between each top level section."""
    lines = value.splitlines()
    output = []

    for line_no, line in enumerate(lines):
        if line_no and not line.startswith(' '):
            output.append('')

        output.append(line)

    return '\n'.join(output) + '\n'


if __name__ == '__main__':
    main()
