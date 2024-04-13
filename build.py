#!/usr/bin/env python3

import os
import subprocess
import pathlib
import platform
import zipfile
import urllib.request
import shutil
import hashlib
import argparse
import sys

windows = platform.platform().startswith('Windows')
osx = platform.platform().startswith(
    'Darwin') or platform.platform().startswith("macOS")
hbb_name = 'rustdesk' + ('.exe' if windows else '')
exe_path = 'target/release/' + hbb_name
if windows:
    flutter_build_dir = 'build/windows/x64/runner/Release/'
elif osx:
    flutter_build_dir = 'build/macos/Build/Products/Release/'
else:
    flutter_build_dir = 'build/linux/x64/release/bundle/'
flutter_build_dir_2 = f'flutter/{flutter_build_dir}'
skip_cargo = False


def get_arch() -> str:
    custom_arch = os.environ.get("ARCH")
    if custom_arch is None:
        return "amd64"
    return custom_arch


def system2(cmd):
    err = os.system(cmd)
    if err != 0:
        print(f"Error occurred when executing: {cmd}. Exiting.")
        sys.exit(-1)


def get_version():
    with open("Cargo.toml", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("version"):
                return line.replace("version", "").replace("=", "").replace('"', '').strip()
    return ''


def parse_rc_features(feature):
    available_features = {
        'PrivacyMode': {
            'platform': ['windows'],
            'zip_url': 'https://github.com/fufesou/RustDeskTempTopMostWindow/releases/download/v0.3'
                       '/TempTopMostWindow_x64.zip',
            'checksum_url': 'https://github.com/fufesou/RustDeskTempTopMostWindow/releases/download/v0.3/checksum_md5',
            'include': ['WindowInjection.dll'],
        }
    }
    apply_features = {}
    if not feature:
        feature = []

    def platform_check(platforms):
        if windows:
            return 'windows' in platforms
        elif osx:
            return 'osx' in platforms
        else:
            return 'linux' in platforms

    def get_all_features():
        features = []
        for (feat, feat_info) in available_features.items():
            if platform_check(feat_info['platform']):
                features.append(feat)
        return features

    if isinstance(feature, str) and feature.upper() == 'ALL':
        return get_all_features()
    elif isinstance(feature, list):
        if windows:
            # download third party is deprecated, we use github ci instead.
            # force add PrivacyMode
            # feature.append('PrivacyMode')
            pass
        for feat in feature:
            if isinstance(feat, str) and feat.upper() == 'ALL':
                return get_all_features()
            if feat in available_features:
                if platform_check(available_features[feat]['platform']):
                    apply_features[feat] = available_features[feat]
            else:
                print(f'Unrecognized feature {feat}')
        return apply_features
    else:
        raise Exception(f'Unsupported features param {feature}')


def make_parser():
    parser = argparse.ArgumentParser(description='Build script.')
    parser.add_argument(
        '-f',
        '--feature',
        dest='feature',
        metavar='N',
        type=str,
        nargs='+',
        default='',
        help='Integrate features, Windows only.'
             'Available: PrivacyMode. Special value is "ALL" and empty "". Default is empty.')
    parser.add_argument('--flutter', action='store_true',
                        help='Build flutter package', default=False)
    parser.add_argument(
        '--hwcodec',
        action='store_true',
        help='Enable feature hwcodec' + (
            '' if windows or osx else ', need libva-dev, libvdpau-dev.')
    )
    parser.add_argument(
        '--gpucodec',
        action='store_true',
        help='Enable feature gpucodec, only available on windows now.'
    )
    parser.add_argument(
        '--portable',
        action='store_true',
        help='Build windows portable'
    )
    parser.add_argument(
        '--unix-file-copy-paste',
        action='store_true',
        help='Build with unix file copy paste feature'
    )
    parser.add_argument(
        '--flatpak',
        action='store_true',
        help='Build rustdesk libs with the flatpak feature enabled'
    )
    parser.add_argument(
        '--appimage',
        action='store_true',
        help='Build rustdesk libs with the appimage feature enabled'
    )
    parser.add_argument(
        '--skip-cargo',
        action='store_true',
        help='Skip cargo build process, only flutter version + Linux supported currently'
    )
    if windows:
        parser.add_argument(
            '--skip-portable-pack',
            action='store_true',
            help='Skip packing, only flutter version + Windows supported'
        )
    parser.add_argument(
        "--package",
        type=str
    )
    parser.add_argument(
        "-d",
        "--debug",
        action='store_true',
        help='Build debug executable'
    )
    parser.add_argument(
        "-g",
        "--codegen",
        action="store_true",
        help="Run Flutter Rust bridge code generation"
    )
    return parser


# Generate build script for docker
#
# it assumes all build dependencies are installed in environments
# Note: do not use it in bare metal, or may break build environments
def generate_build_script_for_docker():
    with open("/tmp/build.sh", "w") as f:
        f.write('''
            #!/bin/bash
            # environment
            export CPATH="$(clang -v 2>&1 | grep "Selected GCC installation: " | cut -d' ' -f4-)/include"
            # flutter
            pushd /opt
            wget https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_3.0.5-stable.tar.xz
            tar -xvf flutter_linux_3.0.5-stable.tar.xz
            export PATH=`pwd`/flutter/bin:$PATH
            popd
            # flutter_rust_bridge
            dart pub global activate ffigen --version 5.0.1
            pushd /tmp && git clone https://github.com/SoLongAndThanksForAllThePizza/flutter_rust_bridge --depth=1 && popd
            pushd /tmp/flutter_rust_bridge/frb_codegen && cargo install --path . && popd
            pushd flutter && flutter pub get && popd
            ~/.cargo/bin/flutter_rust_bridge_codegen --rust-input ./src/flutter_ffi.rs --dart-output ./flutter/lib/generated_bridge.dart
            # install vcpkg
            pushd /opt
            export VCPKG_ROOT=`pwd`/vcpkg
            git clone https://github.com/microsoft/vcpkg
            vcpkg/bootstrap-vcpkg.sh
            popd
            $VCPKG_ROOT/vcpkg install --x-install-root="$VCPKG_ROOT/installed"
            # build rustdesk
            ./build.py --flutter --hwcodec
        ''')
    system2("chmod +x /tmp/build.sh")
    system2("bash /tmp/build.sh")


# Downloading third party resources is deprecated.
# We can use this function in an offline build environment.
# Even in an online environment, we recommend building third-party resources yourself.
def download_extract_features(features, res_dir):
    import re

    proxy = ''

    def req(url):
        if not proxy:
            return url
        else:
            r = urllib.request.Request(url)
            r.set_proxy(proxy, 'http')
            r.set_proxy(proxy, 'https')
            return r

    for (feat, feat_info) in features.items():
        includes = feat_info['include'] if 'include' in feat_info and feat_info['include'] else []
        includes = [re.compile(p) for p in includes]
        excludes = feat_info['exclude'] if 'exclude' in feat_info and feat_info['exclude'] else []
        excludes = [re.compile(p) for p in excludes]

        print(f'{feat} download begin')
        download_filename = feat_info['zip_url'].split('/')[-1]
        checksum_md5_response = urllib.request.urlopen(
            req(feat_info['checksum_url']))
        for line in checksum_md5_response.read().decode('utf-8').splitlines():
            if line.split()[1] == download_filename:
                checksum_md5 = line.split()[0]
                filename, _headers = urllib.request.urlretrieve(feat_info['zip_url'],
                                                                download_filename)
                md5 = hashlib.md5(open(filename, 'rb').read()).hexdigest()
                if checksum_md5 != md5:
                    raise Exception(f'{feat} download failed')
                print(f'{feat} download end. extract bein')
                zip_file = zipfile.ZipFile(filename)
                zip_list = zip_file.namelist()
                for f in zip_list:
                    file_exclude = False
                    for p in excludes:
                        if p.match(f) is not None:
                            file_exclude = True
                            break
                    if file_exclude:
                        continue

                    file_include = False if includes else True
                    for p in includes:
                        if p.match(f) is not None:
                            file_include = True
                            break
                    if file_include:
                        print(f'extract file {f}')
                        zip_file.extract(f, res_dir)
                zip_file.close()
                os.remove(download_filename)
                print(f'{feat} extract end')


def external_resources(flutter, args, res_dir):
    features = parse_rc_features(args.feature)
    if not features:
        return

    print(f'Build with features {list(features.keys())}')
    if os.path.isdir(res_dir) and not os.path.islink(res_dir):
        shutil.rmtree(res_dir)
    elif os.path.exists(res_dir):
        raise Exception(f'Find file {res_dir}, not a directory')
    os.makedirs(res_dir, exist_ok=True)
    download_extract_features(features, res_dir)
    if flutter:
        os.makedirs(flutter_build_dir_2, exist_ok=True)
        for f in pathlib.Path(res_dir).iterdir():
            print(f'{f}')
            if f.is_file():
                shutil.copy2(f, flutter_build_dir_2)
            else:
                shutil.copytree(f, f'{flutter_build_dir_2}{f.stem}')


def get_features(args):
    features = ['inline'] if not args.flutter else []
    if args.hwcodec:
        features.append('hwcodec')
    if args.gpucodec:
        features.append('gpucodec')
    if args.flutter:
        features.append('flutter')
        features.append('flutter_texture_render')
    if args.flatpak:
        features.append('flatpak')
    if args.appimage:
        features.append('appimage')
    if args.unix_file_copy_paste:
        features.append('unix-file-copy-paste')
    print("features:", features)
    return features


def generate_control_file(control_file_path, version):
    system2('/bin/rm -rf %s' % control_file_path)

    content = """Package: rustdesk
Version: %s
Architecture: %s
Maintainer: rustdesk <info@rustdesk.com>
Homepage: https://rustdesk.com
Depends: libgtk-3-0, libxcb-randr0, libxdo3, libxfixes3, libxcb-shape0, libxcb-xfixes0, libasound2, libsystemd0, curl, libva-drm2, libva-x11-2, libvdpau1, libgstreamer-plugins-base1.0-0, libpam0g, libappindicator3-1, gstreamer1.0-pipewire
Description: A remote control software.

""" % (version, get_arch())
    file = open(control_file_path, "w")
    file.write(content)
    file.close()


def set_flutter_bridge_cpath():
    cmd = '''clang -v 2>&1 | grep "Selected GCC installation" | rev | cut -d ' ' -f1 | rev'''
    try:
        result = subprocess.check_output(cmd, shell=True, universal_newlines=True)
    except subprocess.CalledProcessError as e:
        print(f'An error occurred when executing: "{cmd}": {e}. Exiting.')
        sys.exit(-1)
    os.environ['CPATH'] = result.strip() + '/include'

def build_flutter_deb(version, features, codegen):
    if not skip_cargo:
        if codegen:
            if not "CPATH" in os.environ:
                set_flutter_bridge_cpath()
            system2('flutter_rust_bridge_codegen --rust-input ./src/flutter_ffi.rs --dart-output ./flutter/lib/generated_bridge.dart --c-output ./flutter/linux/Runner/bridge_generated.h')
        system2(f'cargo build --features {features} --lib' + ('' if debug else ' --release'))
    os.chdir('flutter')
    system2(f'flutter build linux --{build_type}')
    system2('/bin/rm -rf tmpdeb/')
    system2('mkdir -p tmpdeb/usr/bin/')
    system2('mkdir -p tmpdeb/usr/lib/rustdesk')
    system2('mkdir -p tmpdeb/etc/rustdesk/')
    system2('mkdir -p tmpdeb/etc/pam.d/')
    system2('mkdir -p tmpdeb/usr/share/rustdesk/files/systemd/')
    system2('mkdir -p tmpdeb/usr/share/icons/hicolor/256x256/apps/')
    system2('mkdir -p tmpdeb/usr/share/icons/hicolor/scalable/apps/')
    system2('mkdir -p tmpdeb/usr/share/applications/')
    system2('mkdir -p tmpdeb/usr/share/polkit-1/actions')
    system2('rm -f tmpdeb/usr/bin/rustdesk || true')
    system2(
        f'cp -r {flutter_build_dir}/* tmpdeb/usr/lib/rustdesk/')
    system2(
        'cp ../res/rustdesk.service tmpdeb/usr/share/rustdesk/files/systemd/')
    system2(
        'cp ../res/128x128@2x.png tmpdeb/usr/share/icons/hicolor/256x256/apps/rustdesk.png')
    system2(
        'cp ../res/scalable.svg tmpdeb/usr/share/icons/hicolor/scalable/apps/rustdesk.svg')
    system2(
        'cp ../res/rustdesk.desktop tmpdeb/usr/share/applications/rustdesk.desktop')
    system2(
        'cp ../res/rustdesk-link.desktop tmpdeb/usr/share/applications/rustdesk-link.desktop')
    system2(
        'cp ../res/com.rustdesk.RustDesk.policy tmpdeb/usr/share/polkit-1/actions/')
    system2(
        'cp ../res/startwm.sh tmpdeb/etc/rustdesk/')
    system2(
        'cp ../res/xorg.conf tmpdeb/etc/rustdesk/')
    system2(
        'cp ../res/pam.d/rustdesk.debian tmpdeb/etc/pam.d/rustdesk')
    system2(
        "echo \"#!/bin/sh\" >> tmpdeb/usr/share/rustdesk/files/polkit && chmod a+x tmpdeb/usr/share/rustdesk/files/polkit")

    system2('mkdir -p tmpdeb/DEBIAN')
    generate_control_file("../res/DEBIAN/control", version)
    system2('cp -a ../res/DEBIAN/* tmpdeb/DEBIAN/')
    md5_file('usr/share/rustdesk/files/systemd/rustdesk.service')
    system2('dpkg-deb -b tmpdeb rustdesk.deb;')

    system2('/bin/rm -rf tmpdeb/')
    system2('/bin/rm -rf ../res/DEBIAN/control')
    os.rename('rustdesk.deb', f'../rustdesk-{version}{type_name}.deb')
    os.chdir("..")
    print(
        f'output location: {os.path.abspath(os.curdir)}/rustdesk-{version}{type_name}.deb')


def build_deb_from_folder(version, binary_folder):
    os.chdir('flutter')
    system2('/bin/rm -rf tmpdeb/')
    system2('mkdir -p tmpdeb/usr/bin/')
    system2('mkdir -p tmpdeb/usr/lib/rustdesk')
    system2('mkdir -p tmpdeb/usr/share/rustdesk/files/systemd/')
    system2('mkdir -p tmpdeb/usr/share/icons/hicolor/256x256/apps/')
    system2('mkdir -p tmpdeb/usr/share/icons/hicolor/scalable/apps/')
    system2('mkdir -p tmpdeb/usr/share/applications/')
    system2('mkdir -p tmpdeb/usr/share/polkit-1/actions')
    system2('rm tmpdeb/usr/bin/rustdesk || true')
    system2(
        f'cp -r ../{binary_folder}/* tmpdeb/usr/lib/rustdesk/')
    system2(
        'cp ../res/rustdesk.service tmpdeb/usr/share/rustdesk/files/systemd/')
    system2(
        'cp ../res/128x128@2x.png tmpdeb/usr/share/icons/hicolor/256x256/apps/rustdesk.png')
    system2(
        'cp ../res/scalable.svg tmpdeb/usr/share/icons/hicolor/scalable/apps/rustdesk.svg')
    system2(
        'cp ../res/rustdesk.desktop tmpdeb/usr/share/applications/rustdesk.desktop')
    system2(
        'cp ../res/rustdesk-link.desktop tmpdeb/usr/share/applications/rustdesk-link.desktop')
    system2(
        'cp ../res/com.rustdesk.RustDesk.policy tmpdeb/usr/share/polkit-1/actions/')
    system2(
        "echo \"#!/bin/sh\" >> tmpdeb/usr/share/rustdesk/files/polkit && chmod a+x tmpdeb/usr/share/rustdesk/files/polkit")

    system2('mkdir -p tmpdeb/DEBIAN')
    generate_control_file("../res/DEBIAN/control", version)
    system2('cp -a ../res/DEBIAN/* tmpdeb/DEBIAN/')
    md5_file('usr/share/rustdesk/files/systemd/rustdesk.service')
    system2('dpkg-deb -b tmpdeb rustdesk.deb;')
    system2('/bin/rm -rf tmpdeb/')
    system2('/bin/rm -rf ../res/DEBIAN/control')
    os.rename('rustdesk.deb', f'../rustdesk-{version}{type_name}.deb')
    os.chdir("..")
    print(
        f'output location: {os.path.abspath(os.curdir)}/rustdesk-{version}{type_name}.deb')


def build_flutter_dmg(version, features):
    if not skip_cargo:
        # set minimum osx build target, now is 10.14, which is the same as the flutter xcode project
        system2(
            f'MACOSX_DEPLOYMENT_TARGET=10.14 cargo build --features {features} --lib' + ('' if debug else ' --release'))
    # copy dylib
    system2(
        f"cp target/{build_type}/liblibrustdesk.dylib target/{build_type}/librustdesk.dylib")
    os.chdir('flutter')
    system2(f'flutter build macos --{build_type}')
    build_folder = 'Debug' if debug else 'Release'
    system2(
        f"create-dmg --volname \"RustDesk Installer\" --window-pos 200 120 --window-size 800 400 --icon-size 100 --app-drop-link 600 185 --icon RustDesk.app 200 190 --hide-extension RustDesk.app rustdesk.dmg ./build/macos/Build/Products/{build_folder}/RustDesk.app")
    os.rename("rustdesk.dmg", f"../rustdesk-{version}{type_name}.dmg")
    os.chdir("..")
    print(
        f'output location: {os.path.abspath(os.curdir)}/rustdesk-{version}{type_name}.dmg')


def build_flutter_arch_manjaro(version, features, codegen):
    if not skip_cargo:
        if codegen:
            if not "CPATH" in os.environ:
                set_flutter_bridge_cpath()
            system2('flutter_rust_bridge_codegen --rust-input ./src/flutter_ffi.rs --dart-output ./flutter/lib/generated_bridge.dart --c-output ./flutter/linux/Runner/bridge_generated.h')
        system2(f'cargo build --features {features} --lib' + ('' if debug else ' --release'))
    os.chdir('flutter')
    system2(f'flutter build linux --{build_type}')
    os.chdir('../res')
    system2('HBB=`pwd`/.. FLUTTER=1 makepkg -f')


def build_flutter_rhel(version, features, codegen):
    if not skip_cargo:
        if codegen:
            if not "CPATH" in os.environ:
                set_flutter_bridge_cpath()
            system2('flutter_rust_bridge_codegen --rust-input ./src/flutter_ffi.rs --dart-output ./flutter/lib/generated_bridge.dart --c-output ./flutter/linux/Runner/bridge_generated.h')
        system2(f'cargo build --features {features} --lib' + ('' if debug else ' --release'))
    os.chdir('flutter')
    system2(f'flutter build linux --{build_type}')
    if not debug:
        system2(f'strip {flutter_build_dir}/lib/librustdesk.so')
    os.chdir('..')
    system2(f"sed -i 's/Version:    .*/Version:    {version}/g' res/rpm-flutter.spec")
    system2(f"sed -i 's/flutter\\/build\\/linux\\/x64\\/[[:alnum:]][[:alnum:]]*\\/bundle\\//flutter\\/build\\/linux\\/x64\\/{build_type}\\/bundle\\//g' res/rpm-flutter.spec")
    system2('HBB=`pwd` rpmbuild -bb ./res/rpm-flutter.spec')
    system2(
        f'mv -f $HOME/rpmbuild/RPMS/x86_64/rustdesk-{version}-0.x86_64.rpm ./rustdesk-{version}{type_name}-rhel.rpm')
    print(
        f'output location: {os.path.abspath(os.curdir)}/rustdesk-{version}{type_name}-rhel.rpm')


def build_flutter_suse(version, features, codegen):
    if not skip_cargo:
        if codegen:
            if not "CPATH" in os.environ:
                set_flutter_bridge_cpath()
            system2('flutter_rust_bridge_codegen --rust-input ./src/flutter_ffi.rs --dart-output ./flutter/lib/generated_bridge.dart --c-output ./flutter/linux/Runner/bridge_generated.h')
        system2(f'cargo build --features {features} --lib' + ('' if debug else ' --release'))
    os.chdir('flutter')
    system2(f'flutter build linux --{build_type}')
    if not debug:
        system2(f'strip {flutter_build_dir}/lib/librustdesk.so')
    os.chdir('..')
    system2(f"sed -i 's/Version:    .*/Version:    {version}/g' res/rpm-flutter-suse.spec")
    system2(f"sed -i 's/flutter\\/build\\/linux\\/x64\\/[[:alnum:]][[:alnum:]]*\\/bundle\\//flutter\\/build\\/linux\\/x64\\/{build_type}\\/bundle\\//g' res/rpm-flutter-suse.spec")
    system2('HBB=`pwd` rpmbuild -bb ./res/rpm-flutter-suse.spec')
    system2(
        f'mv -f $HOME/rpmbuild/RPMS/x86_64/rustdesk-{version}-0.x86_64.rpm ./rustdesk-{version}{type_name}-suse.rpm')
    print(
        f'output location: {os.path.abspath(os.curdir)}/rustdesk-{version}{type_name}-suse.rpm')


def build_flutter_windows(version, features, skip_portable_pack):
    if not skip_cargo:
        system2(f'cargo build --features {features} --lib' + ('' if debug else ' --release'))
        if not os.path.exists(f'target/{build_type}/librustdesk.dll'):
            print("cargo build failed, please check rust source code.")
            exit(-1)
    os.chdir('flutter')
    system2(f'flutter build windows --{build_type}')
    os.chdir('..')
    shutil.copy2(f'target/{build_type}/deps/dylib_virtual_display.dll',
                 flutter_build_dir_2)
    if skip_portable_pack:
        return
    os.chdir('libs/portable')
    system2('pip3 install -r requirements.txt')
    debug_arg = '--debug ' if debug else ''
    system2(
        f'python3 ./generate.py {debug_arg}-f ../../{flutter_build_dir_2} -o . -e ../../{flutter_build_dir_2}rustdesk.exe')
    os.chdir('../..')
    if os.path.exists('./rustdesk_portable.exe'):
        os.replace(f'./target/{build_type}/rustdesk-portable-packer.exe',
                   './rustdesk_portable.exe')
    else:
        os.rename(f'./target/{build_type}/rustdesk-portable-packer.exe',
                  './rustdesk_portable.exe')
    print(
        f'output location: {os.path.abspath(os.curdir)}/rustdesk_portable.exe')
    if os.path.exists(f'./rustdesk-{version}-install.exe'):
        os.replace('./rustdesk_portable.exe', f'./rustdesk-{version}{type_name}-install.exe')
    else:
        os.rename('./rustdesk_portable.exe', f'./rustdesk-{version}{type_name}-install.exe')
    print(
        f'output location: {os.path.abspath(os.curdir)}/rustdesk-{version}{type_name}-install.exe')


def main():
    global skip_cargo, exe_path, flutter_build_dir, flutter_build_dir_2, build_type, debug, type_name
    parser = make_parser()
    args = parser.parse_args()

    debug = args.debug
    if debug:
        exe_path = 'target/debug/' + hbb_name
        if windows:
            flutter_build_dir = 'build/windows/x64/runner/Debug/'
        elif osx:
            flutter_build_dir = 'build/macos/Build/Products/Debug/'
        else:
            flutter_build_dir = 'build/linux/x64/debug/bundle/'
        flutter_build_dir_2 = f'flutter/{flutter_build_dir}' 
        build_type = 'debug'
        type_name = '-debug'
    else:
        build_type = 'release'
        type_name = ''
    if os.path.exists(exe_path):
        os.unlink(exe_path)
    if os.path.isfile('/usr/bin/pacman'):
        system2('git checkout src/ui/common.tis')
    version = get_version()
    features = ','.join(get_features(args))
    flutter = args.flutter
    codegen = args.codegen
    if not flutter:
        if codegen:
            print(f"Error: --codegen is invalid without --flutter.")
            sys.exit(-1)
        system2('python3 res/inline-sciter.py')
    print(f'Skip cargo: {args.skip_cargo}')
    if args.skip_cargo:
        skip_cargo = True
    portable = args.portable
    package = args.package
    if package:
        build_deb_from_folder(version, package)
        return
    res_dir = 'resources'
    external_resources(flutter, args, res_dir)
    if windows:
        # build virtual display dynamic library
        os.chdir('libs/virtual_display/dylib')
        system2('cargo build' + ('' if debug else ' --release'))
        os.chdir('../../..')

        if flutter:
            build_flutter_windows(version, features, args.skip_portable_pack)
            return
        if not skip_cargo:
            system2('cargo build' + ('' if debug else ' --release') + ' --features ' + features)
            # system2(f'upx.exe target/{build_type}/rustdesk.exe')
            os.rename(f'target/{build_type}/rustdesk.exe', f'target/{build_type}/RustDesk.exe')
        pa = os.environ.get('P')
        if not debug and pa:
            # https://certera.com/kb/tutorial-guide-for-safenet-authentication-client-for-code-signing/
            system2(
                f'signtool sign /a /v /p {pa} /debug /f .\\cert.pfx /t http://timestamp.digicert.com  '
                f'target\\{build_type}\\rustdesk.exe')
        else:
            print('Not signed')
        os.makedirs(res_dir, exist_ok=True)
        shutil.copy2(f'target/{build_type}/RustDesk.exe', res_dir)
        os.chdir('libs/portable')
        system2('pip3 install -r requirements.txt')
        debug_arg = '--debug ' if debug else ''
        system2(
            f'python3 ./generate.py {debug_arg}-f ../../{res_dir} -o . -e ../../{res_dir}/rustdesk.exe')
        os.chdir('../..')
        if os.path.exists(f'./rustdesk-{version}{type_name}-sciter-install.exe'):
            os.replace(f'./target/{build_type}/rustdesk-portable-packer.exe', f'./rustdesk-{version}{type_name}-sciter-install.exe')
        else:
            os.rename(f'./target/{build_type}/rustdesk-portable-packer.exe', f'./rustdesk-{version}{type_name}-sciter-install.exe')
        print(
            f'output location: {os.path.abspath(os.curdir)}/rustdesk-{version}{type_name}-sciter-install.exe')
    elif os.path.isfile('/usr/bin/pacman'):
        # pacman -S -needed base-devel
        system2(f"sed -i 's/pkgver=.*/pkgver={version}/g' res/PKGBUILD")
        if debug:
            system2('''sed -i "s/options=\([^\)][^\]*\)/options=\(\'\!strip\' \'libtool\' \'staticlibs\' \'debug\'\)/g" res/PKGBUILD''')
        else:
            system2('''sed -i "s/options=\([^\)][^\]*\)/options=\(\'strip\' \'\!libtool\' \'\!staticlibs\' \'\!debug\'\)/g" res/PKGBUILD''')
        system2(f'sed -E -i "s/flutter\/build\/linux\/x64\/(debug|release)\/bundle/flutter\/build\/linux\/x64\/{build_type}\/bundle/g" res/PKGBUILD')
        system2(f'sed -E -i "s/\/target\/(debug|release)\//\/target\/{build_type}\//g" res/PKGBUILD')
        if flutter:
            build_flutter_arch_manjaro(version, features, codegen)
            os.chdir('..')
            system2(f'mv -f res/rustdesk-{version}-0-x86_64.pkg.tar.zst rustdesk-{version}{type_name}-manjaro-arch.pkg.tar.zst')
        else:
            if not skip_cargo:
                system2('cargo build' + ('' if debug else ' --release') + ' --features ' + features)
            system2('git checkout src/ui/common.tis')
            if not debug:
                system2(f'strip target/{build_type}/rustdesk')
            system2('ln -sf res/pacman_install && ln -sf res/PKGBUILD')
            system2('HBB=`pwd` makepkg -f')
            system2(f'mv -f rustdesk-{version}-0-x86_64.pkg.tar.zst rustdesk-{version}{type_name}-manjaro-arch.pkg.tar.zst')
        print(
            f'output location: {os.path.abspath(os.curdir)}/rustdesk-{version}{type_name}-manjaro-arch.pkg.tar.zst')
        # pacman -U ./rustdesk.pkg.tar.zst
    elif os.path.isfile('/usr/bin/yum'):
        if flutter:
            build_flutter_rhel(version, features, codegen)
        else:
            if not skip_cargo:
                system2('cargo build' + ('' if debug else ' --release') + ' --features ' + features)
            if not debug:
                system2(f'strip target/{build_type}/rustdesk')
            system2(
                "sed -i 's/Version:    .*/Version:    %s/g' res/rpm.spec" % version)
            system2('HBB=`pwd` rpmbuild -ba res/rpm.spec')
            system2(
                f'mv -f $HOME/rpmbuild/RPMS/x86_64/rustdesk-{version}-0.x86_64.rpm ./rustdesk-{version}{type_name}-rhel.rpm')
            print(
                f'output location: {os.path.abspath(os.curdir)}/rustdesk-{version}{type_name}-rhel.rpm')
            # yum localinstall rustdesk.rpm
    elif os.path.isfile('/usr/bin/zypper'):
        if flutter:
            build_flutter_suse(version, features, codegen)
        else:
            if not skip_cargo:
                system2('cargo build' + ('' if debug else ' --release') + ' --features ' + features)
            if not debug:
                system2(f'strip target/{build_type}/rustdesk')
            system2(
                "sed -i 's/Version:    .*/Version:    %s/g' res/rpm-suse.spec" % version)
            system2('HBB=`pwd` rpmbuild -ba res/rpm-suse.spec')
            system2(
                f'mv -f $HOME/rpmbuild/RPMS/x86_64/rustdesk-{version}-0.x86_64.rpm ./rustdesk-{version}{type_name}-suse.rpm')
            print(
                f'output location: {os.path.abspath(os.curdir)}/rustdesk-{version}{type_name}-suse.rpm')
    else:
        if flutter:
            if osx:
                build_flutter_dmg(version, features)
                pass
            else:
                # system2(
                #     f'mv target/{build_type}/bundle/deb/rustdesk*.deb ./flutter/rustdesk.deb')
                build_flutter_deb(version, features, codegen)
        else:
            if not skip_cargo:
                system2('cargo bundle' + ('' if debug else ' --release') + ' --features ' + features)
            if osx:
                system2(
                    f'strip target/{build_type}/bundle/osx/RustDesk.app/Contents/MacOS/rustdesk')
                system2(
                    f'cp libsciter.dylib target/{build_type}/bundle/osx/RustDesk.app/Contents/MacOS/')
                # https://github.com/sindresorhus/create-dmg
                build_name = '-debug' if debug else ''
                system2(f'/bin/rm -rf rustdesk.dmg rustdesk-{version}{build_name}.dmg')
                pa = os.environ.get('P')
                if not debug and pa:
                    system2('''
    # buggy: rcodesign sign ... path/*, have to sign one by one
    # install rcodesign via cargo install apple-codesign
    #rcodesign sign --p12-file ~/.p12/rustdesk-developer-id.p12 --p12-password-file ~/.p12/.cert-pass --code-signature-flags runtime ./target/release/bundle/osx/RustDesk.app/Contents/MacOS/rustdesk
    #rcodesign sign --p12-file ~/.p12/rustdesk-developer-id.p12 --p12-password-file ~/.p12/.cert-pass --code-signature-flags runtime ./target/release/bundle/osx/RustDesk.app/Contents/MacOS/libsciter.dylib
    #rcodesign sign --p12-file ~/.p12/rustdesk-developer-id.p12 --p12-password-file ~/.p12/.cert-pass --code-signature-flags runtime ./target/release/bundle/osx/RustDesk.app
    # goto "Keychain Access" -> "My Certificates" for below id which starts with "Developer ID Application:"
    codesign -s "Developer ID Application: {0}" --force --options runtime  ./target/release/bundle/osx/RustDesk.app/Contents/MacOS/*
    codesign -s "Developer ID Application: {0}" --force --options runtime  ./target/release/bundle/osx/RustDesk.app
    '''.format(pa))
                system2(
                    f"create-dmg --volname \"RustDesk Installer\" --window-pos 200 120 --window-size 800 400 --icon-size 100 --app-drop-link 600 185 --icon RustDesk.app 200 190 --hide-extension RustDesk.app rustdesk.dmg target/{build_type}/bundle/osx/RustDesk.app")
                os.rename(f'rustdesk.dmg', f'rustdesk-{version}{build_name}.dmg')
                if not debug and pa:
                    system2('''
    # https://pyoxidizer.readthedocs.io/en/apple-codesign-0.14.0/apple_codesign.html
    # https://pyoxidizer.readthedocs.io/en/stable/tugger_code_signing.html
    # https://developer.apple.com/developer-id/
    # goto xcode and login with apple id, manager certificates (Developer ID Application and/or Developer ID Installer) online there (only download and double click (install) cer file can not export p12 because no private key)
    #rcodesign sign --p12-file ~/.p12/rustdesk-developer-id.p12 --p12-password-file ~/.p12/.cert-pass --code-signature-flags runtime ./rustdesk-{1}.dmg
    codesign -s "Developer ID Application: {0}" --force --options runtime ./rustdesk-{1}.dmg
    # https://appstoreconnect.apple.com/access/api
    # https://gregoryszorc.com/docs/apple-codesign/stable/apple_codesign_getting_started.html#apple-codesign-app-store-connect-api-key
    # p8 file is generated when you generate api key (can download only once)
    rcodesign notary-submit --api-key-path ../.p12/api-key.json  --staple rustdesk-{1}.dmg
    # verify:  spctl -a -t exec -v /Applications/RustDesk.app
    '''.format(pa, version))
                else:
                    print('Not signed')
                print(
                    f'output location: {os.path.abspath(os.curdir)}/rustdesk-{version}{build_name}.dmg')
            else:
                # build deb package
                if not skip_cargo:
                    system2(
                        f'mv -f target/{build_type}/bundle/deb/rustdesk_{version}_amd64.deb ./rustdesk.deb')
                system2('/bin/rm -rf tmpdeb')
                system2('dpkg-deb -R rustdesk.deb tmpdeb')
                system2('mkdir -p tmpdeb/usr/share/rustdesk/files/systemd/')
                system2('mkdir -p tmpdeb/usr/share/icons/hicolor/256x256/apps/')
                system2('mkdir -p tmpdeb/usr/share/icons/hicolor/scalable/apps/')
                system2(
                    'cp res/rustdesk.service tmpdeb/usr/share/rustdesk/files/systemd/')
                system2(
                    'cp res/128x128@2x.png tmpdeb/usr/share/icons/hicolor/256x256/apps/rustdesk.png')
                system2(
                    'cp res/scalable.svg tmpdeb/usr/share/icons/hicolor/scalable/apps/rustdesk.svg')
                system2(
                    'cp res/rustdesk.desktop tmpdeb/usr/share/applications/rustdesk.desktop')
                system2(
                    'cp res/rustdesk-link.desktop tmpdeb/usr/share/applications/rustdesk-link.desktop')
                os.system('mkdir -p tmpdeb/etc/rustdesk/')
                os.system('cp -a res/startwm.sh tmpdeb/etc/rustdesk/')
                os.system('mkdir -p tmpdeb/etc/X11/rustdesk/')
                os.system('cp res/xorg.conf tmpdeb/etc/X11/rustdesk/')
                system2('mkdir -p tmpdeb/DEBIAN')
                generate_control_file("res/DEBIAN/control", version)
                os.system('cp -af res/DEBIAN/* tmpdeb/DEBIAN/.')
                os.system('mkdir -p tmpdeb/etc/pam.d/')
                os.system('cp res/pam.d/rustdesk.debian tmpdeb/etc/pam.d/rustdesk')
                if not debug:
                    system2('strip tmpdeb/usr/bin/rustdesk')
                system2('mkdir -p tmpdeb/usr/lib/rustdesk')
                system2('mv tmpdeb/usr/bin/rustdesk tmpdeb/usr/lib/rustdesk/')
                system2('cp libsciter-gtk.so tmpdeb/usr/lib/rustdesk/')
                md5_file('usr/share/rustdesk/files/systemd/rustdesk.service')
                md5_file('etc/rustdesk/startwm.sh')
                md5_file('etc/X11/rustdesk/xorg.conf')
                md5_file('etc/pam.d/rustdesk')
                md5_file('usr/lib/rustdesk/libsciter-gtk.so')
                system2('dpkg-deb -b tmpdeb rustdesk.deb; /bin/rm -rf tmpdeb/')
                os.rename('rustdesk.deb', f'rustdesk-{version}{type_name}.deb')
                system2('/bin/rm -rf tmpdeb/')
                system2('/bin/rm -rf res/DEBIAN/control')
                print(
                    f'output location: {os.path.abspath(os.curdir)}/rustdesk-{version}{type_name}.deb')


def md5_file(fn):
    md5 = hashlib.md5(open('tmpdeb/' + fn, 'rb').read()).hexdigest()
    system2('echo "%s %s" >> tmpdeb/DEBIAN/md5sums' % (md5, fn))


if __name__ == "__main__":
    main()
