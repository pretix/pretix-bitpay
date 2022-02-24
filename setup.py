import os
from distutils.command.build import build

from django.core import management
from setuptools import setup, find_packages

from pretix_bitpay import __version__

try:
    with open(os.path.join(os.path.dirname(__file__), 'README.rst'), encoding='utf-8') as f:
        long_description = f.read()
except:
    long_description = ''


class CustomBuild(build):
    def run(self):
        management.call_command('compilemessages', verbosity=1)
        build.run(self)


cmdclass = {
    'build': CustomBuild
}


setup(
    name='pretix-bitpay',
    version=__version__,
    description='This plugin allows accepting crypto currency payments in pretix via BitPay-compatible payment '
                'providers.',
    long_description=long_description,
    url='https://github.com/pretix/pretix-bitpay',
    author='Raphael Michel',
    author_email='michel@rami.io',
    license='Apache Software License',

    install_requires=['btcpay-python'],
    packages=find_packages(exclude=['tests', 'tests.*']),
    include_package_data=True,
    cmdclass=cmdclass,
    entry_points="""
[pretix.plugin]
pretix_bitpay=pretix_bitpay:PretixPluginMeta
""",
)
