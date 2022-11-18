yum install -y openssl-devel bzip2-devel libffi-devel xz-devel wget
yum groupinstall -y "Development Tools"

wget https://www.python.org/ftp/python/3.8.12/Python-3.8.12.tgz
tar xvf Python-3.8.12.tgz
cd Python-3.8*/
./configure --enable-optimizations --enable-shared --prefix=/usr LDFLAGS="-Wl,-rpath /usr/lib" 
make python install