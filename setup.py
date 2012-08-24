__author__ = "R B Wilkinson"
__date__ = "29/02/12"
__copyright__ = "(C) 2012 Science and Technology Facilities Council"
__license__ = "BSD - see LICENSE file in top-level directory"
__contact__ = "Philip.Kershaw@stfc.ac.uk"
__revision__ = "$Id$"

try:
    from setuptools import setup, find_packages
except ImportError:
    from ez_setup import use_setuptools
    use_setuptools()
    from setuptools import setup, find_packages

_long_description = """\
This is an OAuth 2.0 server library and WSGI middleware filter.

It supports simple string-based bearer token and a custom extension to enable 
the use of X.509 certificates as tokens.  The latter has been added for a
specialised use case to enable a SLCS (Short-lived Credential Service) to issue 
delegated X.509-based credentials with OAuth.

Prerequisites
=============
This has been developed and tested for Python 2.6 and 2.7.

Installation
============
Installation can be performed using easy_install or pip.  

Configuration
=============
Examples are contained in the examples/ sub-folder:

bearer_tok/:
  This configures a simple test application that uses string based tokens.
slcs/:
  This is a more complex and specialised example that issues X.509 certificate-
  based tokens as part of a Short-lived Credential Service.  The authorisation
  server requires access to a specially configured MyProxyCA service (
  http://grid.ncsa.illinois.edu/myproxy/ca/) configured with a custom PAM to 
  allow issue of credentials. See: 
  http://ndg-security.ceda.ac.uk/browser/trunk/MashMyData/pam_credential_translation
  
The examples should be used in conjunction with the ndg.oauth client package.
"""

setup(
    name =                      'ndg_oauth_server',
    version =                   '0.3.0',
    description =               'OAuth 2.0 server providing MyProxy '
                                'certificates as access tokens',
    long_description =          _long_description,
    author =                    'R. B. Wilkinson',
    maintainer =         	    'Philip Kershaw',
    maintainer_email =          'Philip.Kershaw@stfc.ac.uk',
    #url ='',
    license =                   'BSD - See LICENCE file for details',
    install_requires =[
        "PasteScript",
        "Beaker",
        "WebOb",
        "repoze.who",
        "Genshi",
    ],
    extras_require = {'slcs_support': 'MyProxyClient'},
    packages = find_packages(),
    zip_safe = False,
)
