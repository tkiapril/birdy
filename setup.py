from setuptools import setup
from birdy import __author__, __version__

setup(
    name='birdy',
    version=__version__,
    install_requires=(
        'requests>=1.2.3',
        'requests_oauthlib>=0.3.2',
    ),
    author=__author__[:__author__.find('<')-1],
    author_email=__author__[__author__.find('<')+1:__author__.find('>')],
    license=open('LICENSE').read(),
    url='https://github.com/inueni/birdy/',
    keywords='twitter api tweet birdy search',
    description='birdy is a super awesome Twitter API client for Python.',
    long_description=open('README.rst').read(),
    include_package_data=True,
    packages=(
        'birdy',
    ),
    classifiers=(
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Communications :: Chat',
        'Topic :: Internet',
    )
)
