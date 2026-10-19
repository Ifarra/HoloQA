"""Verify GitHub build provenance before granting a run's installer a release tag."""
import argparse
import json
import re
from pathlib import Path


def verify(metadata, commit, repository, completed=True):
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Expected a full tagged source commit')
    if (metadata.get('head_sha') != commit or metadata.get('path') != '.github/workflows/desktop.yml'
            or metadata.get('repository', {}).get('full_name') != repository
            or metadata.get('head_repository', {}).get('full_name') != repository):
        raise ValueError('Build repository, workflow, or commit does not match the release tag')
    if completed and (metadata.get('status') != 'completed' or metadata.get('conclusion') != 'success'):
        raise ValueError('Only a completed successful Windows build can be promoted')
    return commit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--completed', choices=['true', 'false'], default='true')
    args = parser.parse_args()
    try:
        print('Verified source:', verify(json.loads(args.metadata.read_text()), args.commit,
                                         args.repository, args.completed == 'true'))
    except ValueError as error:
        parser.exit(1, str(error) + '\n')
