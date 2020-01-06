import codecs
from glob import glob
from os.path import abspath, basename, dirname, join, splitext

import setuptools


def read(*parts):
    """Read a file in this repository."""
    here = abspath(dirname(__file__))
    with codecs.open(join(here, *parts), 'r') as file_:
        return file_.read()


setuptools.setup(
    name='metaforward',
    use_scm_version=True,
    description='Data structures that forward attribute and method access to their contents',
    long_description=read('README.md'),
    long_description_content_type='text/markdown',
    author='Masen Furer',
    author_email='m_github@0x26.net',
    url='https://github.west.isilon.com/masenf/metaforward',
    package_dir={"": 'src'},
    packages=setuptools.find_packages('src'),
    py_modules=[splitext(basename(path))[0] for path in glob('src/*.py')],
    python_requires='>=2.7,!=3.0.*,!=3.1.*,!=3.2.*,!=3.3.*,!=3.4.*,<4',
    setup_requires=[
        'setuptools_scm >= 3.3',
    ],
    install_requires=[
        'decorator~=4.4.0',
        'funcsigs~=1.0.2;  python_version ~= "2.7"',
        'future~=0.17.1',
        'six~=1.13.0'
    ],
    classifiers=[
        'Intended Audience :: Developers',
        'Development Status :: 3 - Alpha',
        'Operating System :: OS Independent',
        'License :: OSI Approved :: BSD License',
    ],
)
