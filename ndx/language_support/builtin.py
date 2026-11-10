from .model import Language

PY_KW = ("False None True and as assert async await break class continue def del elif else except finally for "
         "from global if import in is lambda nonlocal not or pass raise return try while with yield self print "
         "len range str int float list dict set tuple open isinstance enumerate zip map").split()
C_KW = ("auto break case char const continue default do double else enum extern float for goto if inline int long "
        "register return short signed sizeof static struct switch typedef union unsigned void volatile while "
        "#include #define #ifdef #endif printf malloc free NULL").split()
CPP_KW = C_KW + ("class namespace template typename public private protected virtual override new delete using "
                 "std string vector cout cin nullptr constexpr auto bool true false try catch throw").split()
RUST_KW = ("as break const continue crate else enum extern false fn for if impl in let loop match mod move mut pub "
           "ref return self Self static struct super trait true type unsafe use where while async await dyn "
           "println! vec! String Vec Option Some None Result Ok Err").split()
JAVA_KW = ("abstract boolean break byte case catch char class const continue default do double else enum extends "
           "final finally float for if implements import instanceof int interface long new package private "
           "protected public return short static super switch this throw throws try void volatile while String "
           "System out println").split()
KT_KW = ("as break class continue do else false for fun if in interface is null object package return super this "
         "throw true try typealias val var when while override open data sealed println").split()
JS_KW = ("async await break case catch class const continue debugger default delete do else export extends false "
         "finally for function if import in instanceof let new null return super switch this throw true try "
         "typeof undefined var void while yield console log require module").split()
TS_KW = JS_KW + "interface type enum implements namespace readonly private public protected number string boolean any".split()
LUA_KW = ("and break do else elseif end false for function goto if in local nil not or repeat return then true "
          "until while print require pairs ipairs table string math").split()
SH_KW = "if then else elif fi for while do done case esac function echo export cd ls grep cat test exit return".split()
HTML_KW = "html head body div span a p ul li ol table tr td th img script style link meta title h1 h2 h3 form input button".split()
CSS_KW = "color background margin padding border display flex grid font-size width height position top left".split()


def register_builtin(reg):
    L = Language
    reg.register(L("python", "Python", [".py", ".pyw"], "python", "#", PY_KW, ["python3"],
                   "", "python3 {file}", ["pylsp"], "pdb", "requirements.txt",
                   "python3 inside a Linux environment or a host install"))
    reg.register(L("lua", "Lua", [".lua"], "lua", "--", LUA_KW, ["lua"], "", "lua {file}",
                   ["lua-language-server"], "", "rockspec", "lua 5.x"))
    reg.register(L("c", "C", [".c", ".h"], "c", "//", C_KW, ["gcc"], "gcc {file} -o {out}", "{out}",
                   ["clangd"], "gdb", "Makefile", "gcc or clang (Linux environment)"))
    reg.register(L("cpp", "C++", [".cpp", ".cc", ".cxx", ".hpp"], "cpp", "//", CPP_KW, ["g++"],
                   "g++ {file} -o {out}", "{out}", ["clangd"], "gdb", "CMakeLists.txt", "g++ or clang++"))
    reg.register(L("rust", "Rust", [".rs"], "rust", "//", RUST_KW, ["rustc"], "rustc {file} -o {out}",
                   "{out}", ["rust-analyzer"], "rust-gdb", "Cargo.toml", "rustc/cargo (Linux environment)"))
    reg.register(L("java", "Java", [".java"], "java", "//", JAVA_KW, ["javac", "java"],
                   "javac {file}", "java {file}", [], "jdb", "pom.xml", "a JDK (Linux environment)"))
    reg.register(L("kotlin", "Kotlin", [".kt", ".kts"], "kotlin", "//", KT_KW, ["kotlinc"],
                   "kotlinc {file} -include-runtime -d {out}.jar", "java -jar {out}.jar", [], "",
                   "build.gradle.kts", "kotlinc + JDK"))
    reg.register(L("javascript", "JavaScript", [".js", ".mjs", ".cjs"], "javascript", "//", JS_KW, ["node"],
                   "", "node {file}", ["typescript-language-server"], "node inspect", "package.json", "Node.js"))
    reg.register(L("typescript", "TypeScript", [".ts", ".tsx"], "typescript", "//", TS_KW, ["tsc", "node"],
                   "tsc {file}", "node {name}.js", ["typescript-language-server"], "", "package.json",
                   "Node.js + tsc"))
    reg.register(L("html", "HTML", [".html", ".htm"], "html", "<!--", HTML_KW, [], "", "", [], "", "",
                   "none (preview via the Server Manager static server)"))
    reg.register(L("css", "CSS", [".css"], "css", "/*", CSS_KW, [], "", "", [], "", "", "none"))
    reg.register(L("shell", "Shell", [".sh", ".bash", ".zsh"], "bash", "#", SH_KW, ["sh"], "", "sh {file}",
                   ["bash-language-server"], "", "", "POSIX sh (Android has /system/bin/sh)"))
    reg.register(L("json", "JSON", [".json"], "json", "", [], [], "", "", [], "", "", "none"))
    reg.register(L("toml", "TOML", [".toml"], "toml", "#", [], [], "", "", [], "", "", "none"))
    reg.register(L("markdown", "Markdown", [".md"], "md", "", [], [], "", "", [], "", "", "none"))
    return reg
