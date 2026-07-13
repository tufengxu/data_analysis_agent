# 0008 — python_analysis 沙箱是 best-effort 容器,非安全边界

- 状态: Accepted (2026-07-02)

## 背景

`tools/python_exec.py` 对模型生成的 Python 代码采用两层防御:

1. **Layer-1 子串黑名单** — 形如 `__import__(` / `os.system` / `open('/etc` 的字面量模式。
2. **Layer-2 AST 静态分析** — 拒危险 import / 危险调用 / 危险路径方法 / dunder 属性。
3. **子进程隔离** — `PYTHONPATH=""`、`cwd` 与 `HOME`/`TMPDIR` 重定向到临时沙箱目录。

生产前审查核实:**AST 黑名单按构造可被绕过**。已被验证、现已在 AST 层补堵的廉价逃逸路径:
动态属性族 `getattr`/`setattr`/`delattr`(`getattr(builtins,"ev"+"al")` 绕过子串黑名单;
`setattr(x,"__class__",...)` 不经属性访问触达 dunder 层)、`builtins`/`ctypes`/`importlib`/
`multiprocessing`/`pickle`/`marshal` 等 FFI/动态导入模块、`globals`/`locals`/`vars`/`__builtins__`
直接引用、以及「别名后调用」(`g = getattr; g(...)`)。`open` 的别名(`f = open; f('/etc/passwd')`)
会跳过只在直接 `open(...)` 调用上触发的路径白名单,故 `open` 只允许作为直接 Call 的函数名出现、
其余引用一律拒。同理,属性形态的文件 API(`f = io.open`、`Path(x).read_bytes` 等)作为引用也一律拒
——直接属性调用(`io.open(path)`)本就已在 Call 分支拒绝,故把引用形态一并拒绝不损失合法用途。
`_wrap_code` 前导码为捕获 stdout 无条件 `import sys`,把 `sys` 名泄漏进用户作用域——`import sys` 虽
在 `dangerous_imports` 内,但 `sys.modules['os'].system(...)` 是完整的用户级 ACE(正是本沙箱要兜住的
「模型写蠢代码误删文件」类)。已把 `sys` 一并加入 `dangerous_names`(Name 引用一律拒),前导码本身经
post-validation 注入、不受影响。
仍**有意保留的残留洞**:

- **计算路径读取**:`pd.read_csv(computed_var)` 的非字面量参数在运行时不经 `allowed_paths` 把关
  (见决策第 1 条的取舍)。
- **标准库「按路径读文件」的整类模块**:已被验证仍可触达的包括 `io.FileIO`、`linecache`、
  `tarfile`、`zipfile`、`shelve`、`dbm`、`configparser` 等——它们以路径字符串打开/读取文件,
  既不在 `dangerous_imports` 也不在 `dangerous_path_methods`。同类的还有隐式 `__main__` 全局
  `__loader__`(`SourceFileLoader.get_data(path)` 可读任意绝对路径,无需 import,比上者更廉价)。
  这是一**开放类**(无法穷举),
  与「计算路径读取」同源;逐个封堵是无尽打地鼠且会带来虚假的安全感,故按决策第 1 条刻意保留为
  已知残留,而非逐个加黑名单。威胁模型改变(需抵御主动逃逸)时,应改用容器化/安全执行运行时,
  而非继续扩这个黑名单。
- **字面相对路径 `../` 穿越也可滑过白名单**:`_validate_path_literal` 对非绝对路径直接放行(允许沙箱
  本地相对读写,如 `open('output.csv')`),故字面量 `pd.read_csv("../../etc/passwd")` 也通过校验。
  与上面同源(路径白名单只检绝对路径),且实际 blast radius 很低——沙箱 `cwd` 是 `$TMPDIR` 深处的
  隔离 tmpdir,短 `../` 链解析回 `$TMPDIR`/`/var` 下的无害位置,长链触发 `ENAMETOOLONG`。刻意不修
  (修了会破坏沙箱本地相对读写),记为同源残留。
- **标准库「反射/内省 → 任意代码执行」的整类模块**:已被验证仍可触达 ACE 的包括 `operator.methodcaller`
  (已封)、`inspect.currentframe().f_builtins`(已封);同类潜在向量还有 `gc`、`types`、`functools`、
  `traceback`(`traceback.walk_stack` 可产生活帧)等反射/内省模块。这也是一**开放类**——黑名单无法穷举
  所有能反射到 builtins/帧/类型的 stdlib 入口。
  策略同上:封掉「廉价且模型会自然写出」的具体入口(operator/inspect),但不追求穷举;威胁模型改变时
  换容器化/安全执行运行时,而非继续扩黑名单。**缓解**:帧属性 sink(`f_builtins`/`f_globals`/`f_locals`/
  `gi_frame`/`cr_frame`/`ag_frame`/`tb_frame`)已在 Attribute 分支统一拒绝——所有「反射到帧再取 builtins」
  的路线无论经哪个模块入口,都在 sink 处被拦,显著缩小该开放类。
- **网络出口 / 远程连接类的客户端库**:`sqlalchemy`(及 DBAPI 驱动 `psycopg2`/`pymysql`/...)、`paramiko`、
  `boto3`/`s3fs`、`pymongo`/`redis` 等「连远程带凭证资源」的库既不在 `dangerous_imports`,也无 AST 规则覆盖。
  默认 venv 里这些库**未安装**(网络出口不可达);但用户环境若装了,模型便可借其外发数据/拉取凭证。这是
  与前两类同构的**开放类**(整个 PyPI 的网络/DB 库无法穷举),刻意不逐个封堵——同上,换容器化/网络命名空间
  才是真解。本类在威胁模型(兜住模型别干蠢事)下优先级低于本地文件误删。
- **父进程信任子进程产物的读边界(非 ACE)**:`agent_result([{"type":"image","path":...}])` 由
  前导码注入、模型可自由调用,父进程(`_compose_result`)会按子进程给出的 `path` 直接 `open`+base64
  装进 `metadata.images`,不经 `allowed_paths` 把关(如 `/etc/hosts` 会被读入上下文)。这是**读取**
  (非任意代码执行),且子进程本就能借 stdout 外发任意用户可读文件(读边界本就不成立)。**刻意不修**:
  合法图表写在沙箱 `cwd`(tmpdir)而非 `allowed_paths`,朴素 allowlist 会误杀合法产物;真要收紧须把
  `cwd` 透传给 `_compose_result` 做「`cwd` 或 `allowed_paths`」判定,价值/复杂度比在本威胁模型(单用户
  本地 CLI、读的是用户自己可读的文件)下不划算,记为已知残留。威胁模型改变时再处理。

威胁模型经确认是**单用户本地 CLI**:沙箱要兜住的是「模型写出蠢代码误伤用户文件」,
不是「抵御恶意租户」。残留洞在该模型下 blast radius = 用户自己的进程权限。

## 决策

**沙箱定位为 best-effort 容器,明确不是安全边界。** 三条:

1. **不追求黑名单完备。** AST 拒绝集只关「廉价、已知」的逃逸;新增逃逸路径被发现时增量补,
   不承诺不可绕过。残留洞(计算路径读取等)在代码注释与本 ADR 中显式列出,不假装不存在。
2. **不在沙箱里跑不可信租户代码。** 多租户/不可信输入场景必须上独立隔离(Docker + seccomp +
   `network=none` + cgroups)或 RestrictedPython 这类安全执行运行时——本沙箱不承担该职责。
3. **资源限制按路径分级,且平台差异诚实标注。** 仅**一次性(stateless)子进程**经 `preexec_fn`
   设 `RLIMIT_FSIZE`(盘,默认 4GB)/`RLIMIT_CPU`(CPU 秒)/`RLIMIT_AS`(地址空间,best-effort);
   **持久内核(kernel)** 路径**不设 rlimit**——它是累积状态的 REPL,紧的 `RLIMIT_AS`/`RLIMIT_CPU`
   会误杀合法的大 DataFrame 分析,其边界由 manager 的**每请求墙钟超时**+ **OS OOM killer(jetsam)**
   共同保证(子进程被杀即由 `_call_kernel` 的降级链重启并报告状态丢失)。故合法的大文件导出
   (如 `df.to_parquet` >4GB)应走默认的 kernel 路径(无 FSIZE 上限);stateless 的 FSIZE 上限
   只在内核不可用的降级场景生效。`RLIMIT_AS` 在 macOS 上无法下调(Darwin 一旦映射完共享库就拒绝降限),
   故仅 Linux 生效,macOS 上静默退化为 no-op——这是已验证的平台事实,非缺陷。

## 理由

AST 黑名单 + 子进程隔离对**本地 CLI 工程 ergonomics** 是对的取舍:零额外运行时依赖、
启动快、对 pandas/matplotlib 这类重库透明。Docker/RestrictedPython 在本威胁模型下是过度工程。
但「黑名单」与「安全边界」是两件事:把可绕过的防御当边界,会让未来 agent/用户错判风险、
据此做错误推理(例如认为「过得了 AST 校验 = 安全」)。**诚实标注残留洞 > 追一个无穷尽的黑名单。**
资源限制同理:给持久 REPL 套 `RLIMIT_AS` 会破坏正常大分析,而 jetsam+超时已经兜住失败,
所以分路径处理而非一刀切。

## 影响

- **未来 agent 不得把沙箱当安全边界推理**;新增「危险名字」时优先加进 AST 拒绝集并在本 ADR
  追记,但不要声称「这下完备了」。
- **不可信/多租户场景不在本项目范围**;需要时另起独立隔离层,不扩本沙箱。
- 计算路径读取的运行时把守(注入 pandas `read_*` 守卫)是有意的**已知残留**,记于此,
  非紧急;价值/复杂度比在本威胁模型下不划算,留待威胁模型改变时再做。
- 资源限制的 macOS 平台限制(Linux-only 的 `RLIMIT_AS`)写进了 `python_exec._apply_rlimits`
  的注释;改平台支持时同步更新本 ADR。
- 相关测试见 `tests/test_sandbox_hardening.py`:钉死已知逃逸路径被拒 + `RLIMIT_FSIZE` 真能 bound
  盘写 + 合法代码无误杀(回归保护)。
