"""Setup script for odin_workshop python package."""

import sys
from setuptools import setup, find_packages
import versioneer

with open('requirements.txt') as f:
    required = f.read().splitlines()

setup(name='odin_snmp',
      version=versioneer.get_version(),
      cmdclass=versioneer.get_cmdclass(),
      description='SNMP packet counter adapter',
      url='https://github.com/stfc-aeg/odin-snmp',
      author='Lukasz Kowalski',
      author_email='lukasz.kowalski@stfc.ac.uk',
      packages=find_packages(),
      install_requires=required,
      zip_safe=False,
)