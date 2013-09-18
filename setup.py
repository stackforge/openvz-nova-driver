# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import setuptools


setuptools.setup(
    name='openvz-nova-driver',
    version='0.1.0',
    description='The OpenVZ Driver for Nova',
    author='Rackspace',
    packages=setuptools.find_packages(exclude=['debian', 'etc']),
    include_package_data=True,
    # i dont know where to put this package to make sure its built
    classifiers=[
        'Development Status :: 4 - Beta',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python :: 2.6',
        'Environment :: No Input/Output (Daemon)',
    ]
)
