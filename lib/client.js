// ---------------------------------------------------------------------------
// 录题工作流 插件版 · Client 半边（原生 Harness 面板，方案 A）
//
// 全部功能以插件原生 React 面板实现，不再 iframe 嵌入旧页面。
// 数据经宿主 /wf-plugin/api/* 透明代理到 Python 后端（与原工作台同一套接口），
// 因此数据逻辑完全复用既有实现，只重写前端，保证高效、准确、风格统一。
//
// 注册：main（keyed：wf-plugin，整页面板）、sidebar.panellist（侧边栏入口）。
// 视图：
//   · 试卷审核（默认）：左栏试卷列表 + 统计；右栏进入某批次审阅
//   · 审阅：原文预览（文字/图片/表格/音频）、小题列表、标签编辑、待人工处理
//   · 上传：新建批次并推进
//   · 模型与设置：左侧四项导航（模型设置 / 上传设置 / OCR 识别 / 听力切分）
// ---------------------------------------------------------------------------
window.__ModuleLoader__.load({
  id: 'md-upload-tiku',
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;

    var API = '/wf-plugin/api';
    // Embed the supplied WebP so the host can render it without a separate asset route.
    var BRAND_LOGO = 'data:image/webp;base64,UklGRv4VAABXRUJQVlA4WAoAAAAQAAAAxwAAxwAAQUxQSAQSAAAB95cREfCHbduyKdn/gdJpocjY3d3d3d3YhN1y2935PGB3d3e3N3a3EnYrmMi1LjDDPedxXtfwd0T/IUqSVLfZggp+JJlBAk6/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEDP2RZZbZ2d9h6wAmvtvfcO66y60Mw9Jr+fdZlvvR2P/v8Njz//7iefHsfQTz55+/lHrz2vtv1ac3WYbH53ho32v3r4yHFFURRFURRFcR1PNaL4+4uXLttn7Wkmd5/NuuuFb/1WFEVRFI1mbCh3tc7y86tn7jDjZOsWXuest1pc/bSk8fw1/KRVppzsvNN9yyu/KoqiSE8602cXrzdZWacr/390URSxhLA+PmPJycV+cuArRVGEEyV7dq/ebX+08PnfF0WOxNlGnTpX2x4td21RFGUkcbcZtmjbHS1/d1FkSybAGxZpm6NFbiyKjMmGeNnsbW/U+4KiKCFZ/sxxU7SxPmDCKZU5OSk/3LEtjZZ5vihyJy/n3XO2lVG7Y8YX+ZMZ9Pf920Zf/KWiKCHZUR9oC//z4P+KUpKfdeyuVR9NM3EmSkkptFdUuwRfYURRVkrBfX2BCve9i6K0lMP756aV7ecVJaYs4iHVHHW8vSgzpSFfXMU+7YQzt9SUx3xP58r1fh8X5aZE6Be7VawvOKGOKzllUr8zfaX6fL8UZc7AXbxWKvbn07f9Pmb0C3decd6x67PNsRdcdc/LX/9REv4XfSvTm8roIx8/ac8Vp2u1FOjQd9UBpz9Xxsr4uCq/MMMXuXW/vm/giu0iGp1WP+CRH3KbvNShpN7q1Xst8yq6bKNeOVz6bH3d13ltHqxEHsqpOe72rbtnPAf7P5a3Wq1AvySj46iT586ttOi5P2aUqpXeD8gn+OE+Pcqwmu6IUfm8tiy5r5zNbvSA9mWJdT3yt1xm/82Z1DMunl9zyZ3SvUy3GS/O5fbZlGW65apJb12g7DWzzKOZ9G4pUez4PFo/7VyFvfjAv/IY7lGa1XJ5pG7uV40zfq48m//42UtymuLzLCfVvtWpHY/N0uolGV2YpWpYpkq1/PpZDrFDS/FZIUuZ0lulHkpmfT2HaSn/6tMMMhcjuZW1gO7KoPpMCTJDMrgcg8oNhmaQ3S7/qfhf3ORgqthOi9v+0jW3SIZba5csm/hqA8+84eGXDuP0Fx64/rQ9Vpo1B+thcd+zM/el4hr7hMHn2+HaV/5tiXgjz7UkGPvipVvPUf6wKW9ejJfv0b976PPjGjEOneSumuSjxwbPFIQeVqmyaPVEh4ZTEoPe6O6/W4NsfXX8dsOa5S6leXMmfILcH0LepZk5IQn79iZlWt+Rsa9VYuW+2RtJN03ipX1u7QD8NL9EvefLl3oQ/9+50nkXfyLx5k++Re8KlJsrR3N7tr5q+AgMHdmJSV9qvw9MNzgyaj5PrtwXhL8ymXaeCZt6ciJbxt19kiWijw//z9TnDrKP7pxKu9VvRXpCW9+XqyeXMX9M4p56A02dJ2cHs1agDAwkuIXvmeqxe1B+vyy94w8x8utTYS8poglhHZtq8nzM/p0s2T4FvGEV0DOR9coinBjXSYkqswZ/rJgjj8e4ByWiXlHEEwQ7JVHmrOAml6HPND528ib3LClh2Om32GND53gOjIkmVjEnFzkSRts7zWf/GOkW8bwQgn4tdR1nSZxtlTSj0SHQm8J95pjlBkmgCxTVyM/TJyntGzu6ukQzOMT8URJnuw8yJQPck0lO3X8MgW4czQMh5L2SOC8oMiUH3UFJUqeGOC8K9i5jI8Q/dkzhXLbIlSx4sybt2eNCW3Yw64QEz0/i/DjLxNzKy1nwnkrSujPEOX+DZL+DFk5axeE5+d9RJ510+sbscNpJJw25ZiJk9t1r4xDmPrE8F+F9PYWy2y/NM5IR9Zso5adJZ/DXEcwbQ71nqEw/OAXz6AlXNm85fF4Yc8dms1ZRy/pgtZDd3AmUXX+Nzsf4Pq1xLhbOJykTsXrIaq5IDojgvp1COSg8H/e3DvpW/OOUffv7COXWLZK/7j8jhfL9idORufY6KAz6aIrbTRHK4yMZHqFdLwFy+ebZiNXCCedc3/Fh1NkS5PaIQN7ZsucuQMb0SHlMiJ+7KWL3h1GPSyCdJwL5fiCLR2BfSlkyP4QnY7kUsa1boIYWf0rdOCoA+Vuf9GwaMTs7AXLd8Fy8N0EsZWf9KQy7UALqrRHIpZN7h9Bv7pRAeXZ4Ko5OU7s0DHtAAukREcitktPv3Lxz/kJ4KuZsQNhxigYIK4dhb0vQ2yxUmk+ZmkVvjHyzW+uQff6IzsTjjRAP27MRw4dR2i86ZP3imX1Ts+YzkUWWcsKHr2j/RoifPtSI4dhm3Fj1nLCNRWrHGxZJzfbvBFDvTYA8pMU8RDax3o1moPixEe5c4WyZIPhG5FZbNTX7jQigXpwAeUl0Hq5tRLhRK48aT0V5awmC9wUYX90sNcdEjt+TEyAfis7D2o0IhxXFkEYUu0d5r0wQvDrA+OGuqTnt9wDqvim3WXAaRrZrRDiyldO799gg8MMpsxFg/Hxwi+QtcHZPKJ1GT5yGTNXs0kVRjOvXiOPmIPAr7RLuzkjheEJqbo9obdc6Y1P0tlygVf+Gd946QeBfE/7U4RHIc1JTGz68Xq/X6/V6vV6v1+v1er1er9fr9eGvJhy/Uz/+ar1er6dyvnFVQ8KXm01vb1ipPfh6hPj1W6Zo3XDDN5Jn49XHG56HAAAAAAAAAACAyCPInONb3Hq9AAAAAAAAAAAAAECbysCWt95GbUp1g6OOqdVqtVqtVqvVarVarVar1Wq1Wu3o42dtnbHbwcfVarVUzFM3aQj4SkrBtuPJAeQTB7Rv3XDhk49KZTzu4JVSc3eopM38lcEN1+87n3/22WefffFUx1wV+h8JXzkoAnl+i+Q76Zr3uYSTbpofI5A/T5irRjV2zy6dO3fu3LVHw31j+lDeTfjKkADj7yen5ry/A6gDWjI23O3ejszB0PjCeSDC/FgC5EkBxq8OSdU64ecA6lEJkE9H5mCpeLaIMN88CWTDcirA+NHuqVqHfh1AvSAB8sZIuZVhI+v0c+RZJmtV8MZWqVq7vB9AvWMSyDwPMEck7MhZr+1uCYz1AOPTqze2aljYR1CHJ0BunojYXMM1padhrRcQXDZhA/gmiXGSM2TJVKtl7w2g/tCxdci5xycjPprniEy/437q3TrhzOMCkzFsjlSp2YdG1v18rUO2/zCZcKe05NvEn0wgXDsyF0O6pkp1PiLCukUC5bXJhXKrFVzypUrWOyWBcN/IXOyQbrVthPW4BMidUgGvzFV7PZ5KvWoC4GWRuVglXWrFCOtdCZB9/kwEXCNX+idCj+6YAPhaYCr+mSNdaoaxAdjvp0ygvC+N74vG9eX+I0aMGDHiJC5tAbBNQ6Sev+eqQPr+G5iKER0DV+fDyI9lEiB3TsM7MUXjWp5sAfBQY6Zr0qhXSiw5MuxFKWd2hPawBMguv6bQ/T1142vY4OLe1bKGbky9WFqBkzITF0Zm4txIjorQPpFCeX7SOT1NU1NTU1NTU1NTU98TGmWSKrdvA6h+TSNSqA9IujNDtX8kGyfStryI0yVQzpJC99svv48ZM2bMmDFjxvw+4TZplEkQfhnTgOqHsSnQ3RPUFgo9WiweyUzjIrj9UzDvLIqiKIqiKIo0z4ZpDBipTVPMjo5QftchtA2/G+G9L4Vy3lKTtKanTTF7M4J5f8PjJO9p99cMSZjVypEtvVrZhEKYtVi2DwEflILZ658q5Zv2STtYiHPlWGYMAX+QxDmoSlk/xarTj0mYk25VHYJ1yishw7WSOIdXJ7cnSe0e4rwjWm8dH0J+IIlz1nFVyQ/dk6TeDoHuEc0yMcmFkkB3LSEZy9p1Y6BN4QJ4RIj57jTQYdXIkIlGKSV2CPSFeCF/Xkxz3jTSp6qQm9J81o6RHhjP0kXs4zTS7h+Xn2cSZ+CNEOn4mcTbBzHT5dNI+40uO290S7PZLob6uAypxbjfTURtGhVPGb3j9zHW7VtJ6mSMj4Hvk3psv1dmnu2WqHL2BPvQ4dtZjnZPjHxc30TWnk9HEzpH2zWLpDwoBFfAhbJkjYnoJZS37S4uK6e0kE8rsKP7dZ72XpB9YDLtgL9CSa8eNk+WODWo/ohM2bMIvjV3Mu7CL+bPfem79krR82HtXOnwXZD+w3bpwAeOzZuv+0/kT13l3wfF3yFXOzR6Ga4PAM92Q3LS9oipA/hPR723TEvimfFTVODQCPJSNyQmoQ64dP4I+4VR6/fJ1w4uyrwSS1zyczzfnDtfCHz/sPQWE5Lxx3dhhxVj1H0HPR3Knw/1nypGvWVY+W1ytv5hibFLNOBOPT4H3P35pIyXcP+kDB/c2n+uKPF68UeL1eVtH4Ytfl28MXli+b3UrsPufP3rP4tLebAYO3r47RfusHiHOO36zT1amWbOqkW8LZqJv/dM8/Vlpvn69ZwEMz6O+044NXO3u+Miv60AAAAAgOyyW2To5+VXmWHcBJf4hlW9PjiD6vcdS5DZr8hgc3DV+mk5TDcsReeVHDqXta/SqFuDIjb0cFGO0HxFDqFX5q1OX/6zHJY/92lWKuOtLO33vavSj/gvi+SmpUk9WmSRunmWKozmf2yCYZbVU57WVL83a2XY+CvwZm18Hr/PytwE1iiKPGaPrlTuaN3huezmK3e5FLncblywxH3l/mxq25d8q91ZZNO7fIFytJa7PZ/XuWUvmfZv5hP879qV80tt2Fx0ZVsy5a/9ph+KjJLP7Zn1YJl5/1dyGr3fuQJ78ZL/Fjk9v716kx55ZKbZ7uZfs+r8NGsljsZ1iqLI6vrlLXuEd7TFBtz3XQuVjEtl4Yqc8dsVRe43xr93+b6r90lb5f3WOeiGjyZq5O6rVqbm6l8URZHfeuynDww7euDm6646Pf1WX3/LfY699JERf5VjsFaFauCDi6IoiqIoytEfxj3lcm9XqUeS3ZL8M55oPFQq9kZUqm1f5gQMpdEjTQm/sSYVaxuNq2KyPOksT+XaCt+3xXwwsSatYJvtrbaXx3pRydbxthJTHjAVHRzbtjKYCreNf2k7+Wzi3690m+XBtpLrmv9GxQe1f9tCfh2AKrRwmfxC9XNv8+NBmxgc9le188MgtJU2/wPZk/dv9kUbGmzxVlXzwrrQtgYHfFPFfLwT2l6b5rRfq5Zvj+yENjnoe/J3VcoXQ3pDWx1Md9gHVckbg7tCpkFVi9YdHqlAxt2xGdDW31jw9E/KzVvHzgEtB23+6+te9X1ZGTl0ZWDy8Ua39c/7IHvGv3nmqh2Byc1HC+9/08jUJD0PXDloTsDk6Y12i+5+3nNftDJTw7i7ld3k06dO33F+AJO1r3ZYcJNB59zx/Hvf/NGiUOPp5n8w5qu3nr/lzL02nLc9wOTxo6al1tn58DMuvfqhh/bh2Ifuv+rS0w7dfp3FZwQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIDJav0FVlA4INQDAADwHwCdASrIAMgAPlEmkkYjoiGhJ5DYkHAKCWMGcGaBeAP0AIYimP4d+Dv7//wDdtuH/jB+M3QIaBHX7YA3jnIx8ABCPaurrNAusmPyj2A0ZiEflJirkBq4/BZysj7jUhxRs2m5b+ZrbtfXxMLXT0akTvD70EjWgNOtuDCx+WunWHKUeH5XxuoC2XxMLXT0anfBgIrY5x/YllZhY/LXT0akxHxouM6xgaqngwsflh0rIKxr3rc3dPRqeDCvyhl/hhu6ejU8GFexfgnQFJUreqKlrp6NTwYV8qRKGVsardo04xuNTwYWPywOF/VJqt2i1JB1OxOaHiXnDN3PVg1VPK1c1xFjgrSenIAA/v5V/mrJwaVFnX+iVzNv/7/8kwRz/x/9CEGtbTxn+czmwti4uu5i/x49gg5EO5dY+f9DlBfRh8Cmq15R0QcNCJ8qul2lXvks06pTDQX0XW15xz/BCES0CLA///E5Q83CIdCohRL9FUWIu9SW3YHqRHdPyy4/RfVtSYhsiY8M62ZCYn/W3r0Vefjj/h5hkih/fJnQS1VNYgqPZ8pT//4m72b1icRdCehvt+cNfuPh+19f/deFa/fr/n/lL/6/4Sl8PT+P9j2at0ctv/9/21pX+/+P+Ev/v/kIno3/W3i1j/9jLfpc/clvT//Vi36uf2FJCBieyWQf7Kv9dXv2/6f/fq3l7Z/9l//RgX5nf1MrZ/+aIvzO/oYK36Suf3/6/fvpAB0P+C//rRF+f/9DMD//owL8//6jEZ7g/6uf7UXSZK/kMXu2y/S5/cK8jJX8aipW/t/P//YeQ8YUP4sxV8xj8zv9Y3ls+fiAthrLHx0hZjJoBaGUEpksPAHh+Ir+Z3+1F5GSv45Extsvz//3CtTC7/FcZucM+Z3+w3/GYv4hiF9hr8//9Y3yExfxQSr++7Wx+T/nirfvJO3c4J3P6hP8aoJdlyAqT+/zmgg2r91/5++/sX73sZW8D/+67a1+/f/P/CX/1/y/2RtoRKr8ZPfc8r56F3z/9/4VpX+v+P+Uv/v/h/qtFRv8PkvlB/VHH//02/af+Tv5/4/4rVQ6Je//owL9XP6isAj96/91/8Tvx/z/z/Pz10n/5oi/M7+hbllKpUQ6tXOgWBjz80l571ad35yZceFhbLe+c7gUpfDcaBmB4hetl7ANpm2DM2/+g/qRSt83+w/0I5uqP8mF/gjT6u4Hfbsz+Qp2eof/3TU/swVih+KBf7I3lM1w4C3/Ar37LNn1/oQYZvnHK6UZqtssiqI5fSzjRKLI5WFm879G9tZkaYPBVTQEL1UgAA==';

    var TEXT = {
      title: '录题系统',
      brandTitle: '木牍题库系统',
      review: '试卷审核',
      upload: '上传试卷',
      settings: '模型与设置',
      paperList: '需要审核的试卷',
      noPaper: '还没有试卷批次，去「上传试卷」新建一份。',
      unitGroup: '题组',
      unitQ: '小题',
      unitHuman: '待审标签',
      unitOk: '无误',
      loading: '加载中…',
      back: '返回列表',
      reload: '刷新',
      archive: '彻底删除批次',
      advance: '继续处理',
      saveLabels: '保存标签',
      saved: '已保存',
      saving: '保存中…',
      sourcePreview: '原卷预览',
      contentPreview: '文章大意 · 解析',
      subquestions: '小题',
      labelEditor: '标签与属性',
      findings: '待人工处理',
      noFindings: '没有待人工处理的疑点。',
      type: '题型',
      subtype: '子类',
      difficulty: '难度',
      knowledge: '知识点',
      sourceType: '来源类型',
      source: '来源',
      categories: '分类',
      answer: '答案',
      stem: '题干',
      options: '选项',
      accept: '通过',
      reject: '驳回',
      fixed: '已修改',
      manual: '手动修改',
      close: '关闭',
      empty: '（无）',
      uploadTitle: '试卷名',
      uploadYear: '年份',
      uploadRegion: '地区',
      uploadSubject: '科目',
      uploadFiles: '文件',
      uploading: '上传中…',
      defaultModel: '默认模型（全局）',
      stageModels: '各子 Agent 模型',
      aiSupplementModels: '文章大意、答案解析模型',
      reviewModels: '审核模型',
      follow: '跟随默认',
      writeEffective: '写入生效配置',
      deleteConfirm: '彻底删除批次',
    };

    // 统一视觉：全部用 Harness 主题 token，随浅/深色主题自动适配。
    // 刻度（全站共用，改一处即可整体协调）：圆角 10/8/6/999，间距 4/6/8/10/12/14/16，
    // 字号 11.5/12/12.5/13/13.5/14/14.5，字重 500/600/700。
    var T = {
      root: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-base)', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.55 },
      tabbar: {
        display: 'flex', alignItems: 'center', gap: 12, height: 52,
        // Harness supplies the space needed for window controls and the sidebar opener.
        paddingInlineStart: 'max(18px, var(--dsh-frame-leading-clearance, 0px))',
        paddingInlineEnd: 18,
        borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', flexShrink: 0,
      },
      brand: { display: 'flex', alignItems: 'center', gap: 9, marginRight: 6, flexShrink: 0 },
      brandMark: { width: 26, height: 26, objectFit: 'contain', flexShrink: 0 },
      brandName: { fontSize: 16, fontWeight: 600, letterSpacing: '.01em', whiteSpace: 'nowrap' },
      segWrap: { display: 'inline-flex', alignItems: 'center', gap: 3, padding: 3, borderRadius: 10, background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l1)' },
      tab: (active) => ({
        padding: '6px 14px', borderRadius: 7, cursor: 'pointer', border: 'none', fontSize: 12.5,
        fontWeight: active ? 600 : 500, whiteSpace: 'nowrap', letterSpacing: '.01em',
        color: active ? 'var(--wf-brand)' : 'var(--dsw-alias-label-secondary)',
        background: active ? 'var(--dsw-alias-bg-base)' : 'transparent',
        boxShadow: active ? '0 1px 3px rgba(16,24,40,.1)' : 'none',
        transition: 'color .18s cubic-bezier(.2,.8,.2,1), background .18s cubic-bezier(.2,.8,.2,1)',
      }),
      iconBtn: { width: 30, height: 30, padding: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', borderRadius: 8, cursor: 'pointer', fontSize: 15, lineHeight: 1, color: 'var(--dsw-alias-label-secondary)', background: 'transparent', border: '1px solid transparent', transition: 'background .18s, color .18s' },
      body: { flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden', background: 'var(--dsw-alias-bg-base)' },
      toolbar: { display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', minHeight: 48, borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', flexShrink: 0, flexWrap: 'wrap' },
      toolbarTitle: { fontWeight: 600, fontSize: 13.5 },
      paperRow: (active) => ({
        margin: '0 2px 6px', padding: '7px 9px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
        borderRadius: 8, transition: 'background .16s, border-color .16s',
        // 列表保留 Harness 默认字体，题目正文在内容区域使用统一排版。
        fontFamily: 'inherit',
        // 每道题都是独立的一行卡片：细边框把题与题分开，不再像连成一片。
        // 选中态用加深的中性底色 + 左侧强调条，浅色/深色主题都看得清。
        background: active ? 'var(--wf-sel-bg)' : 'var(--dsw-alias-bg-base)',
        border: '1px solid ' + (active ? 'var(--wf-sel-border)' : 'var(--dsw-alias-border-l2)'),
        color: 'var(--dsw-alias-label-primary)',
        boxShadow: active ? 'inset 2px 0 0 var(--dsw-alias-label-primary)' : 'none',
      }),
      main: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' },
      scroll: { flex: 1, overflow: 'auto', padding: 18 },
      card: {
        border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 12, padding: 16, marginBottom: 14,
        background: 'var(--dsw-alias-bg-layer-1)', boxShadow: '0 1px 2px rgba(16,24,40,.04), 0 8px 24px rgba(16,24,40,.035)',
      },
      cardTitle: { fontSize: 14, fontWeight: 600, letterSpacing: '-.01em', margin: '0 0 12px', color: 'var(--dsw-alias-label-primary)', display: 'flex', alignItems: 'center', gap: 8 },
      label: { minWidth: 88, fontSize: 12.5, color: 'var(--dsw-alias-label-secondary)' },
      fieldLabel: { fontSize: 12, fontWeight: 500, letterSpacing: '.01em', color: 'var(--dsw-alias-label-secondary)' },
      input: {
        padding: '8px 11px', borderRadius: 8, fontSize: 13, lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)',
        background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l2)',
        outline: 'none', boxSizing: 'border-box', transition: 'border-color .15s, box-shadow .15s',
      },
      btn: {
        padding: '7px 14px', borderRadius: 8, cursor: 'pointer', fontSize: 12.5,
        color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-layer-2)',
        border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .16s, border-color .16s',
      },
      btnPrimary: {
        padding: '8px 18px', borderRadius: 8, cursor: 'pointer', fontSize: 12.5, letterSpacing: '.01em',
        color: 'var(--wf-brand)', background: 'var(--wf-brand-soft)', border: '1px solid var(--wf-brand-border)',
        transition: 'background .16s, border-color .16s, box-shadow .16s',
      },
      btnDanger: {
        padding: '7px 14px', borderRadius: 8, cursor: 'pointer', fontSize: 12.5,
        color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-layer-2)',
        border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .16s, border-color .16s',
      },
      hint: { fontSize: 12, lineHeight: 1.65, color: 'var(--dsw-alias-label-secondary)' },
      // 成功态同样走中性色：用 ✓ 与主文字色表达「通过」，不再用绿色。
      statusOk: { fontSize: 12, color: 'var(--dsw-alias-label-primary)' },
      statusErr: { fontSize: 12, color: 'var(--dsw-alias-state-error-primary)' },
      badge: (kind) => ({
        fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999, lineHeight: 1.5,
        background: kind === 'warn' ? 'rgba(230,162,60,.14)' : kind === 'info' ? 'rgba(59,130,246,.12)' : 'var(--dsw-alias-bg-layer-2)',
        border: kind === 'warn' ? '1px solid rgba(230,162,60,.35)' : kind === 'info' ? '1px solid rgba(59,130,246,.30)' : '1px solid var(--dsw-alias-border-l1)',
        color: kind === 'warn' ? 'var(--dsw-alias-state-warn-primary)' : kind === 'info' ? '#2563eb' : 'var(--dsw-alias-label-secondary)',
      }),
      empty: { padding: '32px 18px', color: 'var(--dsw-alias-label-secondary)', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: 140 },
      emptyIcon: { width: 40, height: 40, borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l1)', marginBottom: 12 },
      emptyTitle: { fontSize: 13, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' },
      emptyHint: { fontSize: 12, marginTop: 5, maxWidth: 340, lineHeight: 1.7 },
      cardClickable: {
        border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 10, padding: '10px 12px', marginBottom: 8,
        background: 'var(--dsw-alias-bg-layer-1)', boxShadow: '0 1px 2px rgba(0,0,0,.05)', cursor: 'pointer',
        transition: 'border-color .16s, box-shadow .16s',
      },
      paperCardTitle: { fontWeight: 600, fontSize: 13, lineHeight: 1.35, color: 'var(--dsw-alias-label-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
      paperCardMeta: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
      pill: { display: 'inline-flex', alignItems: 'baseline', gap: 3, fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 999, padding: '2px 9px', lineHeight: 1.6, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' },
      pillNum: { fontWeight: 700, fontSize: 12.5, color: 'var(--dsw-alias-label-primary)', fontVariantNumeric: 'tabular-nums' },
      pillWarn: { color: 'var(--dsw-alias-state-warn-primary)', borderColor: 'rgba(230,162,60,.4)', background: 'rgba(230,162,60,.1)' },
      miniBtn: { padding: '3px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 12, color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .16s, border-color .16s' },
      miniBtnPrimary: { padding: '3px 12px', borderRadius: 6, cursor: 'pointer', fontSize: 12, color: 'var(--wf-brand)', background: 'var(--wf-brand-soft)', border: '1px solid var(--wf-brand-border)', transition: 'background .16s, border-color .16s' },
      miniBtnDanger: { padding: '3px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 12, color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .16s, border-color .16s' },
      // 自适应按钮：在按钮组内均分可用宽度，随容器伸缩，不写死宽度。
      adaptiveBtn: { flex: '1 1 0', minWidth: 0, whiteSpace: 'nowrap', textAlign: 'center', padding: '3px 2px', overflow: 'hidden', textOverflow: 'ellipsis' },
      // 弹窗外壳：两个弹窗共用，统一宽度、层级、圆角与阴影（带偏移+模糊）。
      modalOverlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,.42)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 3000, padding: 16 },
      modalCard: (width) => ({ width, maxWidth: '94vw', maxHeight: '90vh', display: 'flex', flexDirection: 'column', background: 'var(--dsw-alias-bg-layer-1)', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 12, boxShadow: '0 18px 50px rgba(0,0,0,.24), 0 2px 8px rgba(0,0,0,.08)', overflow: 'hidden' }),
      modalHead: { display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px', borderBottom: '1px solid var(--dsw-alias-border-l1)', flexShrink: 0 },
      modalBody: { padding: 16, overflow: 'auto', flex: 1 },
      modalFoot: { display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '10px 16px', borderTop: '1px solid var(--dsw-alias-border-l1)', flexShrink: 0 },
      modalTitle: { fontWeight: 600, fontSize: 14.5 },
      tagPill: { fontSize: 12, fontWeight: 600, color: 'var(--wf-brand)', background: 'var(--wf-brand-soft)', borderRadius: 999, padding: '2px 9px', whiteSpace: 'nowrap' },
      // 上传页：分区标题统一字阶与留白（页头标题与流程图图例已移除）。
      sectionTitle: { fontSize: 12, fontWeight: 600, letterSpacing: '.02em', color: 'var(--dsw-alias-label-primary)' },
    };

    // 统一的线性图标：品牌标识与侧边栏入口共用同一笔画/线宽，不用 emoji 充当图标。
    function docIcon(size) {
      const h = require('react').createElement;
      return h('svg', { width: size, height: size, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': 'true' },
        h('path', { d: 'M4 2.5h5.5L12 5v8.5H4z' }),
        h('path', { d: 'M9.5 2.5V5H12' }),
        h('path', { d: 'M6 8h4M6 10.5h4' }));
    }
    function closeIcon() {
      const h = require('react').createElement;
      return h('svg', { width: 12, height: 12, viewBox: '0 0 12 12', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', 'aria-hidden': 'true' },
        h('path', { d: 'M2.5 2.5l7 7M9.5 2.5l-7 7' }));
    }
    // 上传入口图标：与 docIcon 同一笔画/线宽，不用 emoji。
    function uploadIcon(size) {
      const h = require('react').createElement;
      return h('svg', { width: size, height: size, viewBox: '0 0 18 18', fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': 'true' },
        h('path', { d: 'M9 11.5V3.5M9 3.5L5.5 7M9 3.5L12.5 7' }),
        h('path', { d: 'M3.5 11.5v2.25a.75.75 0 0 0 .75.75h9.5a.75.75 0 0 0 .75-.75V11.5' }));
    }

    // 全站共用的空/加载/错误态：同一套图标、字阶与留白；文案由调用方逐字传入。
    function EmptyState(props) {
      const h = require('react').createElement;
      const { title, hint, action, flex, tone, compact } = props;
      const iconColor = tone === 'error' ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-secondary)';
      return h('div', { style: { ...T.empty, ...(flex ? { flex: 1 } : {}), ...(compact ? { minHeight: 0, padding: '10px 12px' } : {}), color: iconColor } },
        compact ? null : h('span', { style: { ...T.emptyIcon, color: iconColor } }, docIcon(18)),
        h('span', { style: compact ? { ...T.emptyTitle, fontSize: 12.5 } : T.emptyTitle }, title),
        hint ? h('span', { style: compact ? { ...T.emptyHint, fontSize: 11.5, marginTop: 3, lineHeight: 1.5 } : T.emptyHint }, hint) : null,
        action || null,
      );
    }
    // 全站共用的表单字段：上标签下控件；标签与控件之间的间距统一。
    function formField(label, control) {
      const h = require('react').createElement;
      return h('label', { style: { display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 } },
        h('span', { style: T.fieldLabel }, label), control);
    }
    // 全站共用的弹窗外壳：标签编辑与原卷修改共用同一套层级、圆角、阴影与头部/底部。
    function ModalShell(props) {
      const h = require('react').createElement;
      const { title, tag, dirty, width, onClose, footer, children } = props;
      return h('div', { onClick: onClose, style: T.modalOverlay },
        h('div', { onClick: (e) => e.stopPropagation(), style: T.modalCard(width), role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
          h('div', { style: T.modalHead },
            h('span', { style: T.modalTitle }, title),
            tag ? h('span', { title: '当前正在修改的题目', style: T.tagPill }, tag) : null,
            dirty ? h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-state-warn-primary)' } }, '未保存') : null,
            h('span', { style: { flex: 1 } }),
            h('button', { className: 'wf-icon-btn', title: TEXT.close, 'aria-label': TEXT.close, onClick: onClose, style: T.iconBtn }, closeIcon()),
          ),
          h('div', { className: 'wf-scroll', style: T.modalBody }, children),
          h('div', { style: T.modalFoot }, ...(Array.isArray(footer) ? footer : [footer])),
        ),
      );
    }

    // 注入一次全局样式：hover/focus/滚动条/块级排版（内联样式表达不了）。
    var STYLE_ID = 'wf-plugin-styles';
    function injectStyles() {
      if (typeof document === 'undefined') return;
      // 热加载（HMR）时模块会重新执行，但旧的 <style> 元素仍留在 <head> 里。
      // 之前“已存在就 return”会导致一直用第一次注入的旧 CSS——改样式后看不到效果。
      // 现在始终用最新 CSS 覆盖：存在则替换其内容，不存在才新建。
      var existing = document.getElementById(STYLE_ID);
      var css = ''
        + '.wf-root *{box-sizing:border-box}'
        // 中性色板：不用蓝色。按钮浅白底 + 中性灰文字，选中态用「加深」的中性底色，
        // 与 Harness 其他按钮/标签一致；浅色主题的 bg-layer-2 本身接近纯白，
        // 直接拿它当选中底色会看不出选择，所以按主题色与底色混合出一个更深的底。
        + '.wf-root{--wf-brand:var(--dsw-alias-label-primary);--wf-brand-strong:var(--dsw-alias-label-primary);--wf-brand-soft:var(--dsw-alias-bg-layer-2);--wf-brand-soft-2:var(--dsw-alias-bg-layer-1);--wf-brand-border:var(--dsw-alias-border-l2);--wf-sel-bg:color-mix(in srgb, var(--dsw-alias-label-primary) 10%, var(--dsw-alias-bg-base));--wf-sel-hover:color-mix(in srgb, var(--dsw-alias-label-primary) 5%, var(--dsw-alias-bg-base));--wf-sel-border:var(--dsw-alias-label-secondary);--wf-flow-line:var(--dsw-alias-border-l2);--wf-flow-line-done:color-mix(in srgb, var(--dsw-alias-label-secondary) 40%, transparent);--wf-flow-ring:var(--dsw-alias-border-l2);--wf-flow-ring-done:color-mix(in srgb, var(--dsw-alias-label-secondary) 42%, transparent);--wf-flow-ring-cur:color-mix(in srgb, var(--dsw-alias-label-secondary) 58%, transparent);--wf-flow-line-live:color-mix(in srgb, var(--dsw-alias-label-secondary) 55%, transparent);--wf-flow-node-idle:var(--dsw-alias-bg-layer-1);--wf-flow-node-done:color-mix(in srgb, var(--dsw-alias-label-secondary) 14%, var(--dsw-alias-bg-base));--wf-flow-node-alert:color-mix(in srgb, var(--dsw-alias-label-secondary) 8%, var(--dsw-alias-bg-base));--wf-flow-node-ring:var(--dsw-alias-border-l2);--wf-flow-node-done-ring:color-mix(in srgb, var(--dsw-alias-label-secondary) 45%, transparent);--wf-flow-node-cur-ring:color-mix(in srgb, var(--dsw-alias-label-secondary) 62%, transparent);--wf-flow-node-done-mark:var(--dsw-alias-label-secondary);--wf-flow-node-cur-mark:var(--dsw-alias-label-primary);--wf-flow-node-warn:var(--dsw-alias-state-warn-primary);--wf-flow-node-error:var(--dsw-alias-state-error-primary)}'
        + '.wf-root button,.wf-root input,.wf-root textarea,.wf-root select{font-family:inherit}'
        + '.wf-root ::selection{background:var(--dsw-alias-bg-layer-2);color:inherit}'
        + '.wf-root input,.wf-root textarea{caret-color:var(--wf-brand)}'
        + '.wf-root button:focus-visible,.wf-root input:focus-visible,.wf-root textarea:focus-visible,.wf-root select:focus-visible,.wf-root a:focus-visible{outline:2px solid var(--wf-brand-border);outline-offset:2px}'
        + '.wf-root button:disabled{opacity:.5;cursor:not-allowed!important;filter:none!important}'
        + '.wf-hoverable{cursor:pointer}'
        + '.wf-hoverable:hover{background:var(--dsw-alias-bg-layer-2)!important}'
        // 只让「题目列表行」响应 hover；原卷正文 .wf-paper 不跟着变色。
        // 选中行不再被 hover 的浅底盖掉，只加深边框。
        + '.wf-paper[data-unit]:not(.wf-active):hover{background:var(--wf-sel-hover)!important;border-color:var(--wf-brand-border)!important;color:var(--dsw-alias-label-primary)!important}'
        + '.wf-paper[data-unit].wf-active:hover{border-color:var(--wf-sel-border)!important}'
        + '.wf-card-hover:hover{border-color:var(--wf-brand-border)!important;box-shadow:0 4px 16px rgba(0,0,0,.09)!important}'
        // 按钮状态：内联 style 的 background/border 优先级更高，hover 必须 !important 才生效。
        + '.wf-btn:not(:disabled):hover{background:var(--dsw-alias-bg-layer-2)!important;border-color:var(--wf-brand-border)!important;color:var(--wf-brand)!important}'
        + '.wf-btn:not(:disabled):active{background:var(--dsw-alias-bg-layer-2)!important}'
        + '.wf-btn-primary:not(:disabled):hover{background:var(--wf-brand-soft-2)!important}'
        + '.wf-btn-primary:not(:disabled):active{background:var(--wf-brand-soft)!important}'
        + '.wf-btn-danger:not(:disabled):hover{background:var(--dsw-alias-bg-layer-1)!important;border-color:var(--dsw-alias-border-l2)!important;color:var(--dsw-alias-label-primary)!important}'
        + '.wf-icon-btn:hover{background:var(--dsw-alias-bg-layer-2)!important;color:var(--dsw-alias-label-primary)!important}'
        + '.wf-icon-btn:active{background:var(--dsw-alias-border-l1)!important}'
        + '.wf-input:focus{border-color:var(--wf-brand-border)!important;box-shadow:0 0 0 3px rgba(0,0,0,.06)!important}'
        + '.wf-scroll::-webkit-scrollbar{width:6px;height:6px}'
        + '.wf-scroll::-webkit-scrollbar-thumb{background:var(--dsw-alias-border-l2);border-radius:999px}'
        + '.wf-scroll::-webkit-scrollbar-thumb:hover{background:var(--dsw-alias-label-secondary)}'
        + '.wf-scroll::-webkit-scrollbar-track{background:transparent}'
        + '.wf-prose{white-space:pre-wrap;line-height:1.85;font-size:13.5px}'
        + '.wf-prose p{margin:.3em 0}'
        + '.wf-img{max-width:100%;margin:8px 0;border-radius:8px;border:1px solid var(--dsw-alias-border-l1)}'
        + '.wf-img:hover{border-color:var(--wf-brand-border)}'
        + '.wf-paper,.wf-content-text,.wf-input.wf-paper-input{font-family:"Times New Roman","SimSun","Songti SC",serif;font-size:13.5px;line-height:1.75}'
        + '.wf-paper{color:var(--dsw-alias-label-primary);word-break:break-word}'
        + '.wf-line{margin:0 0 4px;white-space:pre-wrap}'
        + '.wf-heading{font-weight:700;margin:10px 0 6px}'
        // 填空：试卷标准实线下划线，不用高亮底色
        + '.wf-blank{border-bottom:1.5px solid var(--dsw-alias-label-primary);padding:0 8px;color:inherit;font-weight:600;white-space:nowrap}'
        // 空内容 inline-block 按下边缘对齐文本基线，避免含 &nbsp; 时被 line-height 抬高到字母中部
        + '.wf-fill-blank{display:inline-block;min-width:4.2em;height:0;border-bottom:1.5px solid currentColor;vertical-align:-0.15em;margin:0 1px}'
        + '.wf-fill-line{display:inline-block;width:72%;min-width:240px;max-width:460px;height:0;border-bottom:1.5px solid currentColor;vertical-align:-0.15em}'
        // 表格：试卷黑白线框，表头不加底色只加粗
        + '.wf-table{width:100%;max-width:100%;border-collapse:collapse;table-layout:fixed;margin:8px 0;font:inherit}'
        + '.wf-table td,.wf-table th{border:1px solid var(--dsw-alias-label-primary);padding:5px 10px;vertical-align:top;overflow-wrap:anywhere;word-break:break-word;white-space:normal}'
        + '.wf-table th{font-weight:700;background:transparent;text-align:left}'
        + '.wf-table u{text-decoration:none;border-bottom:1.5px solid var(--dsw-alias-label-primary);padding:0 6px}'
        + '.wf-find{display:block;padding:10px 0;border-top:1px solid var(--dsw-alias-border-l1)}'
        + '.wf-find:first-child{border-top:none}'
        + '.wf-settings-page{container-type:inline-size;display:flex;flex:1;min-width:0;min-height:0}'
        + '.wf-settings-layout{display:grid;grid-template-columns:176px minmax(0,1fr);flex:1;min-width:0;min-height:0}'
        + '.wf-settings-nav{display:flex;flex-direction:column;gap:4px;padding:20px 12px;border-right:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-layer-1);overflow-y:auto}'
        + '.wf-settings-nav-title{font-size:12px;color:var(--dsw-alias-label-secondary);padding:0 12px 12px;margin:0}'
        + '.wf-settings-nav button{display:flex;align-items:center;gap:10px;width:100%;padding:11px 12px;border:0;border-radius:8px;text-align:left;font-size:13px;line-height:1.5;white-space:nowrap;cursor:pointer;background:transparent;color:var(--dsw-alias-label-secondary)}'
        + '.wf-settings-nav button:hover{background:var(--wf-sel-hover)}'
        + '.wf-settings-nav button[aria-selected=true]{background:var(--wf-sel-bg);color:var(--dsw-alias-label-primary);font-weight:600}'
        + '.wf-settings-content{min-width:0;overflow:auto;padding:24px 28px;scrollbar-gutter:stable}'
        + '.wf-settings-section{max-width:800px;margin:0 auto;min-width:0}'
        + '.wf-settings-section[hidden]{display:none}'
        + '.wf-settings-section select{min-width:0;max-width:100%;text-overflow:ellipsis}'
        + '.wf-settings-description{margin:0 0 22px;font-size:12px;line-height:1.7;color:var(--dsw-alias-label-secondary)}'
        + '.wf-settings-section h2{font-size:18px!important;margin-bottom:8px!important}'
        + '.wf-settings-section h3{font-size:13px;font-weight:600;margin:0 0 12px}'
        + '.wf-settings-section .wf-settings-card{box-shadow:none}'
        + '.wf-switch{position:relative;width:42px;height:24px;flex:0 0 auto;padding:0;border-radius:999px;border:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-2);cursor:pointer;transition:background .16s,border-color .16s}'
        + '.wf-switch::after{content:"";position:absolute;top:2px;left:2px;width:18px;height:18px;border-radius:999px;background:var(--dsw-alias-label-secondary);transition:transform .18s cubic-bezier(.2,.8,.2,1),background .16s}'
        + '.wf-switch[aria-checked=true]{background:var(--wf-sel-bg);border-color:var(--wf-sel-border)}'
        + '.wf-switch[aria-checked=true]::after{transform:translateX(18px);background:var(--dsw-alias-label-primary)}'
        + '.wf-switch:disabled{opacity:.5;cursor:not-allowed}'
        + '@container (max-width:640px){.wf-settings-layout{grid-template-columns:144px minmax(0,1fr)}.wf-settings-content{padding:20px 16px}.wf-settings-nav{padding:16px 8px}.wf-settings-nav button{gap:8px;padding:10px 8px}}'
        + '@container (max-width:560px){.wf-settings-section .wf-model-fields{grid-template-columns:minmax(0,1fr)!important;gap:6px!important}.wf-settings-section .wf-model-fields .wf-input{margin-bottom:6px}}'
        + '@container (max-width:440px){.wf-settings-layout{grid-template-columns:112px minmax(0,1fr)}.wf-settings-content{padding:16px 12px}.wf-settings-nav button{font-size:12px;gap:6px;padding:10px 6px}.wf-settings-nav button svg{display:none}.wf-settings-nav-title{padding-left:6px}}'
        // 审核两栏：左（原卷预览）与右（待人工处理）各自独立纵向滚动，不整体滚动。
        //   外层只在面板过窄时横向滚动；grid 限高为一行，两栏各自 overflow-y:auto。
        + '.wf-review-container{flex:1;min-height:0;overflow-x:auto;overflow-y:hidden;display:flex;flex-direction:column}'
        + '.wf-review-layout{display:grid;grid-template-columns:minmax(300px,1fr) minmax(240px,285px);grid-template-rows:minmax(0,1fr);align-items:stretch;gap:12px;padding:12px;min-width:564px;flex:1;min-height:0}'
        + '.wf-review-layout .wf-img{display:block;max-width:100%;max-height:260px;width:auto;height:auto;object-fit:contain;margin:4px 0}'
        // 选项配图：A/B/C/D 固定四列；每张图放入等比例图框，不因原图尺寸改变位置。
        + '.wf-opt-row{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));grid-auto-rows:auto;align-items:start;gap:10px 8px;width:100%;max-width:504px;margin:6px 0}'
        + '.wf-opt-cell{display:flex;flex-direction:column;align-items:stretch;gap:4px;min-width:0;width:100%}'
        + '.wf-opt-cell .wf-opt-label{display:block;min-height:18px;font-weight:600;font-size:13px;line-height:18px;white-space:nowrap}'
        + '.wf-opt-frame{position:relative;display:flex;align-items:center;justify-content:center;box-sizing:border-box;width:100%;height:72px;overflow:hidden;border:1px solid var(--dsw-alias-border-l1);border-radius:6px;background:var(--dsw-alias-bg-base)}'
        + '.wf-opt-frame .wf-img{display:block;width:100%;height:100%;max-width:100%;max-height:100%;object-fit:contain;margin:0;border:0;border-radius:0}'
        + '.wf-opt-frame [role=status]{max-width:100%;max-height:100%;overflow:hidden;overflow-wrap:anywhere;text-align:center}'
        // 题干/阅读插图：居中并限制在统一的自适应矩形中，object-fit:contain 保留整张原图。
        + '.wf-body-image-frame{display:flex;align-items:center;justify-content:center;width:100%;max-width:200px;aspect-ratio:4/3;margin:8px auto;overflow:hidden;border:1px solid var(--dsw-alias-border-l1);border-radius:6px;background:var(--dsw-alias-bg-base)}'
        + '.wf-table-imgcell{vertical-align:top;text-align:left;padding:5px 10px;white-space:normal;overflow-wrap:anywhere;word-break:break-word}'
        + '.wf-table-thumb{display:inline-block;width:36px;height:28px;margin:0 4px 2px 0;vertical-align:middle;overflow:hidden;border:1px solid var(--dsw-alias-border-l1);border-radius:3px;background:var(--dsw-alias-bg-base)}'
        + '.wf-table-thumb .wf-img{display:block;width:100%;height:100%;max-width:100%;max-height:100%;object-fit:contain;margin:0;border:0;border-radius:0}'
        + '.wf-table-thumb [role=status]{max-width:100%;max-height:100%;overflow:hidden;font-size:9px;overflow-wrap:anywhere;text-align:center}'
        + '.wf-body-image-frame .wf-img{display:block;width:100%;height:100%;max-width:100%;max-height:100%;object-fit:contain;margin:0;border:0;border-radius:0}'
        + '.wf-body-image-frame [role=status]{max-width:100%;max-height:100%;overflow:hidden;overflow-wrap:anywhere;text-align:center}'
        + '.wf-review-layout .wf-review-main{min-width:0;max-width:100%;width:auto;min-height:0;max-height:100%;overflow-y:auto;overflow-x:hidden}'
        // 右栏审核内容与左侧预览各自独立纵向滚动。
        + '.wf-review-layout .wf-review-notes{min-width:0;max-width:100%;width:auto;min-height:0;max-height:100%;overflow-y:auto;overflow-x:hidden;align-self:stretch}'
        + '.wf-review-layout .wf-review-main::-webkit-scrollbar,.wf-review-layout .wf-review-notes::-webkit-scrollbar{width:6px;height:6px}'
        + '.wf-review-layout .wf-review-main::-webkit-scrollbar-thumb,.wf-review-layout .wf-review-notes::-webkit-scrollbar-thumb{background:var(--dsw-alias-border-l2);border-radius:999px}'
        + '.wf-review-layout .wf-review-main::-webkit-scrollbar-thumb:hover,.wf-review-layout .wf-review-notes::-webkit-scrollbar-thumb:hover{background:var(--dsw-alias-label-secondary)}'
        + '.wf-review-layout .wf-review-main::-webkit-scrollbar-track,.wf-review-layout .wf-review-notes::-webkit-scrollbar-track{background:transparent}'
        // 内容预览卡（文章大意/解析）：只读；长文按行数截断，展开后保留原文换行。
        + '.wf-content-section{margin-top:10px}'
        + '.wf-content-section+.wf-content-section{border-top:1px solid var(--dsw-alias-border-l1);padding-top:10px}'
        + '.wf-content-head{display:flex;align-items:center;gap:8px;margin-bottom:6px}'
        + '.wf-content-title{font-size:12.5px;font-weight:600;letter-spacing:.01em;color:var(--dsw-alias-label-primary)}'
        + '.wf-content-quote{padding:10px 12px;border:1px solid var(--dsw-alias-border-l2);border-radius:8px;background:color-mix(in srgb, var(--dsw-alias-label-primary) 4.5%, var(--dsw-alias-bg-base))}'
        + '.wf-content-sub-label{display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px;margin-bottom:4px;font-size:11.5px;font-weight:600;color:var(--dsw-alias-label-secondary)}'
        + '.wf-content-text{color:var(--dsw-alias-label-primary);white-space:pre-wrap;overflow-wrap:anywhere}'
        + '.wf-content-clamp{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:6;overflow:hidden}'
        + '.wf-content-clamp-3{-webkit-line-clamp:3}'
        + '.wf-content-rows{margin-top:2px;padding:2px 12px 6px;border:1px solid var(--dsw-alias-border-l2);border-radius:8px;background:color-mix(in srgb, var(--dsw-alias-label-primary) 4.5%, var(--dsw-alias-bg-base))}'
        + '.wf-content-row:first-child{border-top:none}'
        + '.wf-content-row{display:grid;grid-template-columns:max-content minmax(0,1fr);align-items:start;gap:8px;padding:7px 0;border-top:1px dashed var(--dsw-alias-border-l1)}'
        + '.wf-content-row-body{display:grid;grid-template-columns:minmax(0,1fr);gap:4px}'
        + '.wf-content-row-body>.wf-content-ans,.wf-content-row-body>.wf-content-link{justify-self:start}'
        + '.wf-content-num{flex:0 0 auto;min-width:24px;padding:1px 7px;border:1px solid var(--dsw-alias-border-l2);border-radius:6px;font-size:11.5px;font-weight:700;line-height:1.5;text-align:center;color:var(--wf-brand);background:var(--wf-brand-soft)}'
        + '.wf-content-ans{box-sizing:border-box;min-width:0;max-width:100%;white-space:pre-wrap;overflow-wrap:anywhere;padding:0 5px;border:1px solid var(--dsw-alias-border-l2);border-radius:4px;font-size:11px;font-weight:600;line-height:1.6;color:var(--dsw-alias-label-secondary);background:var(--dsw-alias-bg-layer-2)}'
        + '.wf-content-missing{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:6px}'
        + '.wf-content-empty{padding:2px 0}'
        + '.wf-content-row-empty{opacity:.72}'
        + '.wf-content-link{border:0;background:transparent;padding:1px 2px;border-radius:4px;font-size:12px;white-space:nowrap;color:var(--wf-brand);cursor:pointer}'
        + '.wf-content-link:hover{text-decoration:underline}'
        + '.wf-content-link:focus-visible{outline:2px solid var(--wf-brand-border);outline-offset:1px}'
        + '.wf-preitem{white-space:nowrap;flex:0 0 auto}'
        + '.wf-group-head{display:flex;align-items:center;gap:4px;width:100%;padding:8px 10px;margin:4px 0;cursor:pointer;background:var(--dsw-alias-bg-base);border:1px solid var(--dsw-alias-border-l1);border-radius:8px;text-align:left;color:var(--dsw-alias-label-primary);transition:background .16s,border-color .16s}'
        + '.wf-group-head:hover{background:var(--dsw-alias-bg-layer-2);border-color:var(--wf-brand-border)}'
        // 表单：占位符用浅灰（不用浏览器默认深灰），下拉框统一手型与聚焦反馈。
        + '.wf-root input::placeholder,.wf-root textarea::placeholder{color:var(--dsw-alias-label-secondary);opacity:.72}'
        + '.wf-root select{cursor:pointer}'
        // 上传文件区：原生 input 只保留可聚焦性，视觉全部交给自定义浅灰控件。
        + '.wf-file-input{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}'
        + '.wf-file-drop{position:relative;display:flex;align-items:center;gap:10px;width:100%;padding:7px 12px;border:1px dashed var(--dsw-alias-border-l2);border-radius:10px;background:transparent;cursor:pointer;transition:border-color .18s,background .18s,box-shadow .18s}'
        + '.wf-file-drop:hover{border-color:var(--wf-brand-border);background:var(--dsw-alias-bg-layer-1)}'
        + '.wf-file-drop:focus-within{border-color:var(--wf-brand-border);box-shadow:0 0 0 3px rgba(16,24,40,.06)}'
        + '.wf-file-drop.wf-dragover{border-color:var(--wf-brand);background:var(--wf-brand-soft)}'
        + '.wf-file-label{font-size:12.5;font-weight:500;letter-spacing:.01em;color:var(--dsw-alias-label-secondary);transition:color .18s}'
        + '.wf-file-drop:hover .wf-file-label,.wf-file-drop:focus-within .wf-file-label{color:var(--wf-brand)}'
        + '.wf-file-chip{display:inline-flex;align-items:center;gap:6px;max-width:100%;padding:4px 6px 4px 10px;border:1px solid var(--dsw-alias-border-l1);border-radius:999px;background:var(--dsw-alias-bg-layer-1);font-size:12;color:var(--dsw-alias-label-primary);line-height:1.4}'
        + '.wf-file-chip button{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;padding:0;border:none;border-radius:50%;background:transparent;color:var(--dsw-alias-label-secondary);cursor:pointer;transition:background .16s,color .16s}'
        + '.wf-file-chip button:hover{background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary)}'
        // 上传页布局：左列（上传 + 排队区）/ 右列（流程图），窄面板自动堆叠。
        + '.wf-upload-layout{flex:1;min-height:0;display:flex;flex-direction:column;padding:8px 20px 16px;overflow:auto;container-type:inline-size}'
        + '.wf-upload-columns{flex:1;min-height:0;display:grid;grid-template-columns:minmax(320px,.92fr) minmax(0,1.08fr);grid-template-rows:minmax(0,1fr);gap:20px}'
        + '.wf-upload-columns>*{min-height:0}'
        + '.wf-upload-left{min-width:0;display:flex;flex-direction:column;min-height:0;overflow:auto;padding-right:20px;border-right:1px solid var(--dsw-alias-border-l1)}'
        + '.wf-diag-fab{transition:transform .16s ease,box-shadow .16s ease,background .16s ease}'
        + '.wf-diag-fab:hover{transform:translateY(-1px);box-shadow:0 6px 16px rgba(217,48,37,.18)}'
        + '.wf-diag-fab:active{transform:translateY(0)}'
        + '.wf-diag-fab:focus-visible,.wf-diag-popover button:focus-visible{outline:2px solid var(--dsw-alias-state-error-primary);outline-offset:2px}'
        + '@keyframes wf-diag-pop{from{opacity:0;transform:translateY(6px) scale(.97)}to{opacity:1;transform:translateY(0) scale(1)}}'
        + '.wf-diag-popover{animation:wf-diag-pop .16s ease-out}'

        // 运行中节点动效：外圈脉冲放大淡出 + 中心点呼吸明暗，表示该阶段正在跑。
        + '@keyframes wf-node-pulse{0%{transform:scale(1);opacity:.75}70%{transform:scale(1.6);opacity:0}100%{transform:scale(1.6);opacity:0}}'
        + '@keyframes wf-node-breathe{0%,100%{opacity:.3}50%{opacity:1}}'
        + '@keyframes wf-node-glow{0%,100%{opacity:.35}50%{opacity:.9}}'
        + '.wf-node-pulse{transform-box:fill-box;transform-origin:center;animation:wf-node-pulse 1.5s ease-out infinite}'
        + '.wf-node-dot{transform-box:fill-box;transform-origin:center;animation:wf-node-breathe 1.2s ease-in-out infinite}'
        // 运行中连线的流动虚线：沿路径偏移动画，示意数据正流入该阶段。
        + '@keyframes wf-flow-dash{to{stroke-dashoffset:-22}}'
        + '.wf-flow-live{animation:wf-flow-dash 1s linear infinite}'
        // 排队区完成退场与顺位递补动画。
        + '@keyframes wf-queue-exit{0%{opacity:1;transform:translateX(0) scale(1);max-height:76px;margin-bottom:3px;padding-top:9px;padding-bottom:9px}38%{opacity:.62;transform:translateX(10px) scale(.985);max-height:76px;margin-bottom:3px;padding-top:9px;padding-bottom:9px}100%{opacity:0;transform:translateX(18px) scale(.96);max-height:0;margin-bottom:0;padding-top:0;padding-bottom:0}}'
        + '.wf-queue-row-exiting{overflow:hidden;pointer-events:none;background:rgba(22,163,74,.08)!important;animation:wf-queue-exit .42s cubic-bezier(.4,0,.2,1) forwards}'
        + '@keyframes wf-queue-slide-up{0%{transform:translateY(8px)}100%{transform:translateY(0)}}'
        + '.wf-queue-row-promoted{animation:wf-queue-slide-up .32s cubic-bezier(.2,.8,.2,1)}'
        + '@keyframes wf-queue-rank-up{0%{transform:scale(1)}45%{transform:scale(1.22);background:rgba(37,99,235,.16);color:#1d4ed8}100%{transform:scale(1)}}'
        + '.wf-queue-rank-up{animation:wf-queue-rank-up .38s cubic-bezier(.2,.8,.2,1)}'
        + '@media (prefers-reduced-motion: reduce){.wf-node-pulse,.wf-node-dot,.wf-flow-live,.wf-operations-status::before,.wf-queue-row-exiting,.wf-queue-row-promoted,.wf-queue-rank-up{animation:none}}'
        + '@container (max-width:840px){.wf-upload-columns{flex:0 0 auto;grid-template-columns:minmax(0,1fr);grid-template-rows:none;gap:18px}.wf-upload-left{padding-right:0;border-right:none;overflow:visible}.wf-flow-card{min-height:420px}}'
        // AI运维独立固定底部卡片：状态胶囊 + 独立可滚动实时动态区。
        + '@keyframes wf-ops-pulse{0%,100%{opacity:.45;transform:scale(.92)}50%{opacity:1;transform:scale(1.12)}}'
        + '.wf-operations-status{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:600;line-height:1.45;white-space:nowrap;border:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-secondary)}'
        + '.wf-operations-status::before{content:"";width:6px;height:6px;border-radius:50%;flex-shrink:0;background:currentColor}'
        + '.wf-operations-status[data-tone="active"]{color:#2563eb;background:rgba(37,99,235,.10);border-color:rgba(37,99,235,.28)}'
        + '.wf-operations-status[data-tone="active"]::before{animation:wf-ops-pulse 1.4s ease-in-out infinite}'
        + '.wf-operations-status[data-tone="waiting"]{color:var(--dsw-alias-state-warn-primary);background:rgba(230,162,60,.12);border-color:rgba(230,162,60,.34)}'
        + '.wf-operations-status[data-tone="ok"]{color:#15803d;background:rgba(22,163,74,.10);border-color:rgba(22,163,74,.28)}'
        + '.wf-operations-status[data-tone="error"]{color:var(--dsw-alias-state-error-primary);background:rgba(220,38,38,.09);border-color:rgba(220,38,38,.30)}'
        + '.wf-operations-feed-item{display:flex;align-items:baseline;gap:6px;padding:4px 7px;border-radius:6px;font-size:11.5px;line-height:1.5;color:var(--dsw-alias-label-primary);background:var(--dsw-alias-bg-base);border:1px solid var(--dsw-alias-border-l1)}'
        + '.wf-operations-feed-time{flex:0 0 auto;font-size:10.5px;color:var(--dsw-alias-label-secondary);font-variant-numeric:tabular-nums}'
        + '.wf-operations-feed-tag{flex:0 0 auto;font-size:10.5px;font-weight:600;padding:0 5px;border-radius:4px;background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-secondary);white-space:nowrap}'
        + '.wf-operations-feed-text{flex:1;min-width:0;overflow-wrap:anywhere;word-break:break-word;color:var(--dsw-alias-label-primary)}'
        // 唯一一处入场动效：切到页面时轻微上浮淡入，不逐个元素重复播放。
        + '@keyframes wf-page-in{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}'
        + '.wf-upload-layout{animation:wf-page-in .22s cubic-bezier(.2,.8,.2,1)}'
        // 修改标签弹窗：左列知识点树 + 右列属性表单。
        + '.wf-label-modal-grid{display:grid;grid-template-columns:minmax(0,1.42fr) minmax(248px,1fr);gap:18px;align-items:start}'
        + '.wf-label-left{display:flex;flex-direction:column;gap:10px;min-width:0}'
        + '.wf-label-right{display:flex;flex-direction:column;gap:12px;min-width:0;padding-left:18px;border-left:1px solid var(--dsw-alias-border-l1)}'
        + '@media (max-width:720px){.wf-label-modal-grid{grid-template-columns:1fr}.wf-label-right{padding-left:0;border-left:none;padding-top:12px;border-top:1px solid var(--dsw-alias-border-l1)}}'
        + '.wf-kp-search-wrap{position:relative;display:flex;align-items:center}'
        + '.wf-kp-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);border:none;background:transparent;color:var(--dsw-alias-label-secondary);cursor:pointer;font-size:13px;line-height:1;padding:2px 5px;border-radius:4px}'
        + '.wf-kp-clear:hover{color:var(--dsw-alias-label-primary);background:var(--dsw-alias-bg-layer-2)}'
        + '.wf-kp-section-head{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:11.5px;color:var(--dsw-alias-label-secondary)}'
        + '.wf-kp-chips{display:flex;flex-wrap:wrap;gap:6px;padding:8px 10px;min-height:38px;border-radius:8px;background:var(--dsw-alias-bg-layer-1);border:1px solid var(--dsw-alias-border-l1);align-items:center}'
        + '.wf-kp-chip{display:inline-flex;align-items:center;gap:5px;max-width:100%;font-size:13px;line-height:1.4;padding:4px 9px;border-radius:6px;background:var(--wf-sel-bg,var(--dsw-alias-bg-layer-2));border:1px solid var(--wf-sel-border,var(--dsw-alias-border-l2));color:var(--dsw-alias-label-primary)}'
        + '.wf-kp-chip.is-locatable{cursor:pointer;transition:background .12s,border-color .12s}'
        + '.wf-kp-chip.is-locatable:hover{background:var(--dsw-alias-bg-base);border-color:var(--dsw-alias-label-secondary)}'
        + '.wf-kp-chip.is-outside{border-color:var(--dsw-alias-state-warn-primary);background:color-mix(in srgb,var(--dsw-alias-state-warn-primary) 10%,transparent)}'
        + '.wf-kp-chip-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
        + '.wf-kp-chip-warn{flex:0 0 auto;font-size:11px;color:var(--dsw-alias-state-warn-primary);font-weight:600;white-space:nowrap}'
        + '.wf-kp-chip-rm{flex:0 0 auto;border:none;background:transparent;cursor:pointer;color:var(--dsw-alias-label-secondary);font-size:13px;line-height:1;padding:0 2px}'
        + '.wf-kp-chip-rm:hover{color:var(--dsw-alias-label-primary)}'
        + '.wf-kp-tree-box{height:290px;overflow:auto;border:1px solid var(--dsw-alias-border-l1);border-radius:8px;background:var(--dsw-alias-bg-base);padding:4px}'
        + '.wf-tree-row{display:flex;align-items:center;gap:8px;width:100%;min-height:32px;padding:5px 8px;border:1px solid transparent;border-radius:6px;background:transparent;color:var(--dsw-alias-label-primary);font-size:14px;line-height:1.4;text-align:left;cursor:pointer;transition:background .12s,border-color .12s,box-shadow .18s}'
        + '.wf-tree-row:hover{background:var(--dsw-alias-bg-layer-1)}'
        + '.wf-tree-row.is-selected{background:var(--wf-sel-bg,var(--dsw-alias-bg-layer-2));border-color:var(--wf-sel-border,var(--dsw-alias-border-l2));font-weight:600}'
        + '.wf-tree-row.is-located{box-shadow:inset 0 0 0 1.5px var(--dsw-alias-label-primary);background:var(--wf-sel-bg,var(--dsw-alias-bg-layer-2))}'
        + '.wf-tree-caret{flex:0 0 18px;width:18px;height:18px;display:inline-flex;align-items:center;justify-content:center;font-size:16px;line-height:1;color:var(--dsw-alias-label-secondary)}'
        + '.wf-tree-check{flex:0 0 16px;width:16px;height:16px;display:inline-flex;align-items:center;justify-content:center;border-radius:3px;border:1px solid var(--dsw-alias-border-l2);font-size:11px;line-height:1;color:transparent;background:var(--dsw-alias-bg-base)}'
        + '.wf-tree-row.is-selected .wf-tree-check{background:var(--dsw-alias-label-primary);border-color:var(--dsw-alias-label-primary);color:var(--dsw-alias-bg-base)}'
        + '.wf-tree-label{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
        + '.wf-tree-count{flex:0 0 auto;font-size:12px;color:var(--dsw-alias-label-secondary);font-variant-numeric:tabular-nums}'
        + '.wf-seg-btn-row{display:grid;gap:8px;width:100%}'
        + '.wf-seg-btn-row.cols-3{grid-template-columns:repeat(3,minmax(0,1fr))}'
        + '.wf-seg-btn-row.cols-2{grid-template-columns:repeat(2,minmax(0,1fr))}'
        + '.wf-seg-btn{display:flex;align-items:center;justify-content:center;height:32px;padding:0 8px;border-radius:7px;border:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-secondary);font-size:12.5px;font-weight:500;cursor:pointer;text-align:center;white-space:nowrap;transition:background .12s,border-color .12s,color .12s}'
        + '.wf-seg-btn:hover{background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary)}'
        + '.wf-seg-btn.is-active{background:var(--wf-sel-bg,var(--dsw-alias-bg-layer-2));border-color:var(--dsw-alias-label-primary);color:var(--dsw-alias-label-primary);font-weight:600}'
        // 原卷修改弹窗专属样式（仅作用域于 ContentModal 内部，不影响其他页面与弹窗）。
        + '.wf-cm-body{display:flex;flex-direction:column;gap:14px}'
        + '.wf-cm-notice{display:flex;align-items:flex-start;gap:8px;padding:9px 12px;border-radius:10px;font-size:12px;line-height:1.6;color:var(--dsw-alias-label-secondary);background:color-mix(in srgb, var(--dsw-alias-label-primary) 3.5%, var(--dsw-alias-bg-base));border:1px solid var(--dsw-alias-border-l1)}'
        + '.wf-cm-notice-dot{width:6px;height:6px;border-radius:50%;background:var(--dsw-alias-label-secondary);margin-top:6px;flex-shrink:0;opacity:.65}'
        + '.wf-cm-section{border:1px solid var(--dsw-alias-border-l1);border-radius:10px;padding:12px 14px;background:var(--dsw-alias-bg-base)}'
        + '.wf-cm-section-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:9px}'
        + '.wf-cm-section-title{font-size:12.5px;font-weight:600;color:var(--dsw-alias-label-primary);display:inline-flex;align-items:center;gap:6px}'
        + '.wf-cm-section-sub{font-size:11.5px;font-weight:400;color:var(--dsw-alias-label-secondary)}'
        + '.wf-cm-meta{font-size:11.5px;color:var(--dsw-alias-label-secondary);font-variant-numeric:tabular-nums}'
        + '.wf-cm-toggle-btn{border:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-secondary);font-size:11.5px;font-weight:500;padding:3px 9px;border-radius:6px;cursor:pointer;transition:background .15s,border-color .15s,color .15s}'
        + '.wf-cm-toggle-btn:hover{background:var(--wf-sel-hover);border-color:var(--wf-brand-border);color:var(--dsw-alias-label-primary)}'
        + '.wf-cm-sub-list{display:flex;flex-direction:column;gap:8px}'
        + '.wf-cm-sub-card{border:1px solid var(--dsw-alias-border-l1);border-radius:10px;background:var(--dsw-alias-bg-base);overflow:hidden;transition:border-color .16s,box-shadow .16s}'
        + '.wf-cm-sub-card.is-open{border-color:var(--dsw-alias-border-l2);box-shadow:0 2px 10px rgba(16,24,40,.05)}'
        + '.wf-cm-sub-head{display:flex;align-items:center;gap:8px;width:100%;padding:9px 12px;border:0;background:transparent;text-align:left;cursor:pointer;color:var(--dsw-alias-label-primary);transition:background .15s}'
        + '.wf-cm-sub-head:hover{background:var(--wf-sel-hover)}'
        + '.wf-cm-sub-card.is-open .wf-cm-sub-head{background:color-mix(in srgb, var(--dsw-alias-label-primary) 3%, var(--dsw-alias-bg-base));border-bottom:1px solid var(--dsw-alias-border-l1)}'
        + '.wf-cm-sub-num{flex:0 0 auto;min-width:26px;text-align:center;font-size:11.5px;font-weight:700;color:var(--wf-brand);background:var(--wf-sel-bg);border:1px solid var(--dsw-alias-border-l1);border-radius:6px;padding:1px 7px;line-height:1.5;font-variant-numeric:tabular-nums}'
        + '.wf-cm-sub-ans{flex:0 0 auto;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;font-weight:600;color:var(--dsw-alias-label-secondary);background:var(--dsw-alias-bg-layer-2);border:1px solid var(--dsw-alias-border-l1);border-radius:999px;padding:1px 8px;line-height:1.55}'
        + '.wf-cm-chevron{flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;color:var(--dsw-alias-label-secondary);transition:transform .16s ease}'
        + '.wf-cm-sub-card.is-open .wf-cm-chevron{transform:rotate(90deg);color:var(--dsw-alias-label-primary)}'
        + '.wf-cm-sub-body{display:flex;flex-direction:column;gap:10px;padding:12px 14px 14px;background:var(--dsw-alias-bg-base)}';
      var el = existing || document.createElement('style');
      el.id = STYLE_ID;
      el.textContent = css;
      if (!existing) document.head.appendChild(el);
    }


    // ---- API helpers（全部走 /wf-plugin/api/*）----
    async function jget(path, signal) {
      const controller = new AbortController();
      let timer;
      let abort;
      const cancelled = new Promise((resolve, reject) => {
        abort = () => { controller.abort(); reject(new DOMException('已取消题目请求', 'AbortError')); };
        if (signal && signal.aborted) abort();
        else if (signal) signal.addEventListener('abort', abort, { once: true });
        timer = setTimeout(() => {
          controller.abort();
          reject(new Error('请求超过15秒，请重试；也可以继续选择其他题目'));
        }, 15000);
      });
      try {
        return await Promise.race([cancelled, (async () => {
          const r = await window.fetch(API + path, { headers: { accept: 'application/json' }, signal: controller.signal });
          if (!r.ok) throw new Error((await r.text()) || r.status);
          return r.json();
        })()]);
      } finally {
        clearTimeout(timer);
        if (signal) signal.removeEventListener('abort', abort);
      }
    }
    async function jpost(path, body) {
      const r = await window.fetch(API + path, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
      if (!r.ok) {
        const text = await r.text();
        let message = text || String(r.status);
        try { const data = JSON.parse(text); message = data.error || data.message || message; } catch (_) {}
        const error = new Error(message);
        error.httpStatus = r.status;
        throw error;
      }
      return r.json();
    }
    // 页面 origin 可能是 dsh-app://app（自定义 scheme），<img>/<audio> 原生加载器
    // 无法访问 /wf-plugin 路由（只有 fetch 被宿主补丁路由）。因此图片/音频必须用
    // 宿主真实 HTTP origin 的绝对 URL。启动时从 /wf-plugin/health 取 origin 缓存。
    var MEDIA_ORIGIN = '';       // 例 http://127.0.0.1:53682
    var mediaOriginPromise = null;
    function ensureMediaOrigin() {
      if (MEDIA_ORIGIN) return Promise.resolve(MEDIA_ORIGIN);
      if (mediaOriginPromise) return mediaOriginPromise;
      mediaOriginPromise = window.fetch('/wf-plugin/health', { headers: { accept: 'application/json' } })
        .then((r) => r.json())
        .then((d) => {
          if (d && d.origin) MEDIA_ORIGIN = String(d.origin).replace(/\/$/, '');
          // origin 拿不到时退回 location.origin（多数 web 场景即 http origin）
          if (!MEDIA_ORIGIN && typeof location !== 'undefined' && location.origin && /^https?:/.test(location.origin)) MEDIA_ORIGIN = location.origin;
          return MEDIA_ORIGIN;
        })
        .catch(() => { mediaOriginPromise = null; return MEDIA_ORIGIN; });
      return mediaOriginPromise;
    }
    // 把后端返回的 /api/... 转成插件代理路径；有真实 http origin 时补成绝对 URL，
    // 这样 <img>/<audio> 原生加载器也能取到（dsh-app:// 下相对路径会 404）。
    function mediaUrl(u) {
      if (!u) return u;
      if (/^https?:/i.test(u) || u.startsWith('data:') || u.startsWith('blob:')) return u;
      const p = u.startsWith('/api/') ? '/wf-plugin' + u : u;
      return MEDIA_ORIGIN ? MEDIA_ORIGIN + p : p;
    }
    // 同一组件内只允许最新请求提交；切题卸载时同时取消网络与结果写回。
    function createRequestGate() {
      let generation = 0;
      let controller = null;
      return {
        begin() {
          if (controller) controller.abort();
          controller = new AbortController();
          const currentController = controller;
          const id = ++generation;
          return { signal: currentController.signal, current: () => id === generation && !currentController.signal.aborted };
        },
        cancel() { generation++; if (controller) controller.abort(); },
      };
    }

    // 桌面端 fetch 由 Harness 代理；img 原生请求不能假定享有相同路由和访问上下文。
    // 本地题图始终经 fetch 获取，再创建当前页面可访问的 Blob URL。
    function imageRequestUrl(value) {
      if (typeof value !== 'string' || !value.trim()) throw new Error('图片地址为空');
      const u = value.trim();
      if (u.startsWith('/api/')) return '/wf-plugin' + u;
      if (u.startsWith('/wf-plugin/')) return u;
      if (/^(https?:|blob:|data:image\/)/i.test(u)) return u;
      throw new Error('不支持的图片地址');
    }
    // ---- 图片缓存 + 同步命中表 + 并发限流预取 ---------------------------------------
    // 同一 URL 的题图只下载/解码一次；IMG_RESOLVED 保存已就绪的 data/blob URL，
    // PreviewImage 首帧即可同步渲染 <img>，彻底消除切题时的“图片加载中…”闪烁。
    var IMG_CACHE = new Map();
    var IMG_RESOLVED = new Map();
    var IMG_CACHE_ORDER = [];      // LRU 次序（先入先出）
    var IMG_CACHE_MAX = 400;       // 最多缓存 400 张（题图很小，约几 MB 量级）
    function imgCacheRemember(url, promise) {
      IMG_CACHE.set(url, promise);
      IMG_CACHE_ORDER.push(url);
      while (IMG_CACHE_ORDER.length > IMG_CACHE_MAX) {
        const old = IMG_CACHE_ORDER.shift();
        if (old !== url) {
          if (IMG_CACHE.has(old)) IMG_CACHE.delete(old);
          if (IMG_RESOLVED.has(old)) IMG_RESOLVED.delete(old);
        }
      }
    }
    function syncCachedImage(value) {
      if (!value) return null;
      let url;
      try { url = imageRequestUrl(value); } catch { return null; }
      if (/^(blob:|data:image\/)/i.test(url)) return url;
      return IMG_RESOLVED.get(url) || null;
    }
    // 提取预览块里的全部图片 URL（含表格单元格配图），明确跳过音频块。
    function collectBlockImageUrls(blocks) {
      const urls = [];
      const seen = new Set();
      const push = (u) => {
        if (typeof u !== 'string' || !u) return;
        if (!seen.has(u)) { seen.add(u); urls.push(u); }
      };
      for (const b of (blocks || [])) {
        if (!b || typeof b !== 'object') continue;
        if (b.type === 'image') {
          push(b.url);
        } else if (b.type === 'table') {
          for (const row of (b.cellImages || [])) {
            if (!Array.isArray(row)) continue;
            for (const cell of row) {
              if (!Array.isArray(cell)) continue;
              for (const im of cell) {
                if (im && typeof im === 'object') push(im.url);
              }
            }
          }
          for (const row of (b.structured || [])) {
            for (const cell of ((row && row.cells) || [])) {
              for (const im of ((cell && cell.images) || [])) {
                if (im && typeof im === 'object') push(im.url);
              }
            }
          }
        }
      }
      return urls;
    }
    // 单个图片请求（不含缓存）：带 503/网络错误重试，独立超时。下载本身不绑定任何
    // 调用方 signal——下载一次、结果进缓存，多个调用方共享。
    async function fetchImageOnce(value) {
      const url = imageRequestUrl(value);
      if (/^(blob:|data:image\/)/i.test(url)) return url;
      const local = url.startsWith('/wf-plugin/api/');
      const requestUrl = local ? url + (url.includes('?') ? '&' : '?') + 'format=data-url' : url;
      const delays = [0, 400, 1000, 2000]; // 冷启动/代理重连时的短退避
      let lastErr = null;
      for (let attempt = 0; attempt < delays.length; attempt++) {
        if (delays[attempt]) await new Promise((r) => setTimeout(r, delays[attempt]));
        const controller = new AbortController();
        let timer;
        const stalled = new Promise((resolve, reject) => {
          timer = setTimeout(() => { controller.abort(); reject(new Error('图片加载超时，请重试；可以继续切换题目')); }, 12000);
        });
        try {
          return await Promise.race([stalled, (async () => {
            const response = await window.fetch(requestUrl, { headers: { accept: local ? 'application/json' : 'image/*' }, signal: controller.signal });
            if (!response.ok) throw new Error('图片请求失败（' + response.status + '）');
            if (local) {
              const data = await response.json();
              if (!data || !/^data:image\/[a-z0-9.+-]+;base64,/i.test(data.src || '')) throw new Error('图片接口未返回有效图片');
              return data.src;
            }
            const blob = await response.blob();
            if (!/^image\//i.test(blob.type)) throw new Error('图片接口未返回图片');
            return URL.createObjectURL(blob);
          })()]);
        } catch (e) {
          lastErr = e;
          if (e && e.name === 'AbortError') throw e; // 内部超时取消，不重试
          const msg = String((e && e.message) || e);
          if (/\b(400|401|403|404)\b/.test(msg)) break; // 非瞬时，放弃
        } finally {
          clearTimeout(timer);
        }
      }
      throw lastErr || new Error('图片加载失败');
    }
    // 取共享下载 Promise（并发去重）：同一 URL 只下载一次，结果/失败都进缓存。
    function sharedImageDownload(url) {
      const cached = IMG_CACHE.get(url);
      if (cached) return cached;
      const p = fetchImageOnce(url).then(
        (src) => { IMG_RESOLVED.set(url, src); return src; },
        (err) => { IMG_CACHE.delete(url); IMG_RESOLVED.delete(url); const i = IMG_CACHE_ORDER.indexOf(url); if (i >= 0) IMG_CACHE_ORDER.splice(i, 1); throw err; }
      );
      imgCacheRemember(url, p);
      return p;
    }
    // 带缓存的取图：命中缓存直接返回；否则共享下载。同时尊重【调用方】的取消与超时——
    // 调用方 abort 只取消它自己的等待（抛 AbortError），不会打断共享下载/污染缓存。
    function fetchPreviewImage(value, signal) {
      let url;
      try { url = imageRequestUrl(value); } catch (err) { return Promise.reject(err); }
      if (/^(blob:|data:image\/)/i.test(url)) return Promise.resolve(url);
      const download = sharedImageDownload(url);
      if (!signal) return download;
      if (signal.aborted) return Promise.reject(new DOMException('已取消图片加载', 'AbortError'));
      let abort;
      const cancelled = new Promise((resolve, reject) => {
        abort = () => reject(new DOMException('已取消图片加载', 'AbortError'));
        signal.addEventListener('abort', abort, { once: true });
      });
      return Promise.race([download, cancelled]).finally(() => {
        try { signal.removeEventListener('abort', abort); } catch {}
      });
    }
    // 限流后台预取：按给定顺序预热整卷配图，控制并发数避免打满代理连接。
    function prefetchImages(values, concurrency) {
      const limit = Math.max(1, Number(concurrency) || 4);
      const queue = [];
      const seen = new Set();
      for (const v of (values || [])) {
        try {
          const url = imageRequestUrl(v);
          if (!/^(blob:|data:image\/)/i.test(url) && !IMG_CACHE.has(url) && !seen.has(url)) {
            seen.add(url);
            queue.push(url);
          }
        } catch {}
      }
      if (!queue.length) return Promise.resolve();
      let cursor = 0;
      const workers = [];
      const workerCount = Math.min(limit, queue.length);
      for (let i = 0; i < workerCount; i++) {
        workers.push((async () => {
          while (cursor < queue.length) {
            const nextUrl = queue[cursor++];
            if (!IMG_CACHE.has(nextUrl)) {
              await fetchPreviewImage(nextUrl).catch(() => {});
            }
          }
        })());
      }
      return Promise.all(workers).then(() => undefined);
    }

    // ---- 整卷单元详情缓存（LRU，最多保留 3 份试卷）----------------------------------
    var UNIT_CACHE = new Map();
    var UNIT_CACHE_ORDER = [];
    var UNIT_CACHE_MAX = 3;
    function unitCacheTouch(batchName) {
      const key = String(batchName || '');
      const idx = UNIT_CACHE_ORDER.indexOf(key);
      if (idx >= 0) UNIT_CACHE_ORDER.splice(idx, 1);
      UNIT_CACHE_ORDER.push(key);
      while (UNIT_CACHE_ORDER.length > UNIT_CACHE_MAX) {
        const old = UNIT_CACHE_ORDER.shift();
        if (old !== key) UNIT_CACHE.delete(old);
      }
    }
    function ensureBatchCacheEntry(batchName) {
      const key = String(batchName || '');
      let entry = UNIT_CACHE.get(key);
      if (!entry) {
        entry = { shared: null, units: new Map(), order: [], promise: null, loaded: false };
        UNIT_CACHE.set(key, entry);
      }
      unitCacheTouch(key);
      return entry;
    }
    function getCachedUnit(batchName, unitId) {
      const entry = UNIT_CACHE.get(String(batchName || ''));
      if (!entry) return null;
      return entry.units.get(String(unitId)) || null;
    }
    function setCachedUnit(batchName, unitId, detail) {
      if (!batchName || !detail) return;
      const entry = ensureBatchCacheEntry(batchName);
      const key = String(unitId);
      entry.units.set(key, detail);
      if (detail.unit_id != null && String(detail.unit_id) !== key) {
        entry.units.set(String(detail.unit_id), detail);
      }
    }
    function invalidateBatchUnits(batchName) {
      const key = String(batchName || '');
      UNIT_CACHE.delete(key);
      const idx = UNIT_CACHE_ORDER.indexOf(key);
      if (idx >= 0) UNIT_CACHE_ORDER.splice(idx, 1);
    }
    function loadBatchUnitsBundle(batchName, opts) {
      const force = !!(opts && opts.force);
      const entry = ensureBatchCacheEntry(batchName);
      if (!force && entry.loaded) return Promise.resolve(entry);
      if (!force && entry.promise) return entry.promise;
      const p = (async () => {
        const delays = [0, 500, 1200];
        let lastErr = null;
        for (let attempt = 0; attempt < delays.length; attempt++) {
          if (delays[attempt]) await new Promise((r) => setTimeout(r, delays[attempt]));
          try {
            const bundle = await jget('/batches/' + encodeURIComponent(batchName) + '/units');
            const shared = (bundle && bundle.shared) || {};
            const rawUnits = (bundle && bundle.units) || {};
            const nextUnits = new Map();
            for (const [uid, u] of Object.entries(rawUnits)) {
              if (!u || typeof u !== 'object') continue;
              const hydrated = { ...shared, ...u };
              nextUnits.set(String(uid), hydrated);
              if (hydrated.unit_id != null && String(hydrated.unit_id) !== String(uid)) {
                nextUnits.set(String(hydrated.unit_id), hydrated);
              }
            }
            entry.shared = shared;
            entry.units = nextUnits;
            entry.order = Array.isArray(bundle && bundle.order) ? bundle.order.map(String) : Array.from(nextUnits.keys());
            entry.loaded = true;
            return entry;
          } catch (e) {
            lastErr = e;
            const msg = String((e && e.message) || e);
            if (/\b(400|401|403|404)\b/.test(msg)) break;
          }
        }
        throw lastErr || new Error('整卷预加载失败');
      })().finally(() => {
        if (entry.promise === p) entry.promise = null;
      });
      entry.promise = p;
      return p;
    }

    function PreviewImage({ url, name, style }) {
      const React = require('react');
      const h = React.createElement;
      const [attempt, setAttempt] = React.useState(0);
      const [image, setImage] = React.useState(() => {
        const src = syncCachedImage(url);
        return src ? { url, src } : null;
      });
      React.useEffect(() => {
        let active = true;
        const syncSrc = syncCachedImage(url);
        if (syncSrc) {
          setImage((prev) => (prev && prev.url === url && prev.src === syncSrc) ? prev : { url, src: syncSrc });
          return () => { active = false; };
        }
        // 已在下载队列中时共享同一个 Promise，避免重复请求。
        const cached = (() => { try { return IMG_CACHE.get(imageRequestUrl(url)); } catch { return null; } })();
        if (cached) {
          cached.then((src) => { if (active) setImage({ url, src }); }).catch((error) => {
            if (active && error && error.name !== 'AbortError') setImage({ url, error: String(error.message || error) });
          });
          return () => { active = false; };
        }
        setImage(null);
        fetchPreviewImage(url).then((src) => {
          if (active) setImage({ url, src });
        }).catch((error) => {
          if (active && error.name !== 'AbortError') setImage({ url, error: String(error.message || error) });
        });
        return () => { active = false; };
      }, [url, attempt]);
      const resolved = (image && image.url === url) ? image : (() => {
        const src = syncCachedImage(url);
        return src ? { url, src } : null;
      })();
      if (!resolved) return h('span', { role: 'status', style: T.hint }, '图片加载中…');
      if (resolved.error) return h('div', { role: 'status', style: { ...T.hint, margin: '8px 0' } },
        '图片未显示：' + (name || '') + '（' + resolved.error + '） ',
        h('button', { className: 'wf-btn', style: T.btn, onClick: () => { try { const u = imageRequestUrl(url); IMG_CACHE.delete(u); IMG_RESOLVED.delete(u); const i = IMG_CACHE_ORDER.indexOf(u); if (i >= 0) IMG_CACHE_ORDER.splice(i, 1); } catch {} setAttempt((n) => n + 1); } }, '重试图片'));
      return h('img', {
        src: resolved.src, alt: name || '试题配图', className: 'wf-img',
        style: style || undefined,
        onError: () => setImage({ url, error: '图片解码失败' }),
      });
    }

    const BoundaryReact = require('react');
    class QuestionBoundary extends BoundaryReact.Component {
      constructor(props) { super(props); this.state = { error: null, retry: 0 }; }
      static getDerivedStateFromError(error) { return { error: String(error.message || error) }; }
      componentDidCatch(error, info) { console.error('[wf-plugin] question render failed', error, info); }
      render() {
        if (!this.state.error) return BoundaryReact.createElement(BoundaryReact.Fragment, { key: this.state.retry }, this.props.children);
        return BoundaryReact.createElement(EmptyState, {
          flex: true, tone: 'error',
          title: '本题显示失败：' + this.state.error + '。可以继续切换其他题目。',
          action: BoundaryReact.createElement('button', { className: 'wf-btn', style: T.btn,
            onClick: () => this.setState((s) => ({ error: null, retry: s.retry + 1 })) }, '重新显示本题'),
        });
      }
    }

    function modelKey(m) { return m ? m.provider + '|' + m.model : ''; }

    // 试卷正文渲染：先转义，再只放行 <u><b><i><strong><em> 与 <br>，把 **x**→粗体、
    // __x__→下划线、全角空格包裹的题号 →下划线填空样式。与原工作台 markLine 口径一致。
    function escHtml(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function paperDisplayText(text, bulkBold) {
      const value = String(text == null ? '' : text);
      if (!bulkBold) return value;
      return value.split('\n').map((line) => {
        // Extracted runs can be adjacent, and assembly can leave empty
        // bold markers at the start of a field. Preserve other inline tags.
        const plain = line.replace(/<\/?(?:u|sup|sub)>|<br\s*\/?>/gi, '').trim();
        const whole = /^(?:\s*\*{2,}[^*]+\*{2,}\s*)+$/.test(plain);
        return whole || /^\*{2,}$/.test(plain) ? line.replace(/\*{2,}/g, '') : line;
      }).join('\n');
    }
    function markLine(text, bulkBold) {
      let out = escHtml(paperDisplayText(text, bulkBold));
      out = out.replace(/&lt;(\/?)(u|b|i|strong|em|br|sup|sub)&gt;/gi, '<$1$2>'); // 放行无属性的原文标记
      // Numbered blanks use one rule, whether Word encoded the digit as text
      // underlining, underscore runs, or both around an inline-marked digit.
      out = out
        .replace(/_{2,}\s*(?:<u>\s*)?(\d{1,3})(?:\s*<\/u>)?\s*_{2,}/g, '\u3000$1\u3000')
        .replace(/<u>[\s\u3000\xa0]*_{2,}\s*(\d{1,3})\s*_{2,}[\s\u3000\xa0]*<\/u>/g, '\u3000$1\u3000')
        .replace(/<u>([\s\u3000\xa0]*)(?:_{1,2}|__)(\d{1,3})(?:_{1,2}|__)([\s\u3000\xa0]*)<\/u>/g, '\u3000$2\u3000')
        .replace(/<u>([\s\u3000\xa0]+)(\d{1,3})[\s\u3000\xa0]+<\/u>/g, '\u3000$2\u3000');
      // __x__ 只还原库侧「题号空档」__数字__（对称后端 normalize_blank_text 的 __26__）；
      // 纯空 ______、单词+空 i______ 都不含数字，不得当标记。否则 __(.+?)__ 会把两段
      // 下划线之间的文字（如 "______ are ______"）误包成 <u>are</u>，或在原有 <u> 内
      // 再套一层 <u>，渲染出「很短、一粗一细两条线」的错乱下划线（用户 2026-11-19 反馈）。
      out = out.replace(/\*\*([\s\S]+?)\*\*/g, '<b>$1</b>').replace(/\*{2,}/g, '').replace(/__(\d{1,3})__/g, '<u>$1</u>');
      // 同一处空档在 Word 里常被拆成多段连续下划线（`<u>　</u><u>16</u><u>　</u><u> </u>`）：
      // 先按整段合并，否则数字不会走填空线样式，尾部空白下划线还会在窄格里换行成孤立「_」。
      out = out.replace(/(?:<u>[\s\u3000\xa0]*<\/u>)*<u>(\d{1,3})<\/u>(?:<u>[\s\u3000\xa0]*<\/u>)*/g, '\u3000$1\u3000');
      out = out.replace(/\u3000(\d{1,3})\u3000/g, '<span class="wf-blank">$1</span>');
      return out;
    }
    function isHeaderRow(row) {
      return Array.isArray(row) && row.length > 0 && row.every((c) => String(c == null ? '' : c).length <= 10);
    }
    const SECTION_RE = /^(?:第[一二三四五六七八九十]+部分|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.|[IVX]{1,4}\.)/;
    function isHeading(text) { return SECTION_RE.test(String(text || '').trim()); }

    function BackendRestartControl(props) {
      const React = require('react');
      const h = React.createElement;
      const [status, setStatus] = React.useState({ running: true, busy: false, deferred: false, restarting: false });
      const [phase, setPhase] = React.useState('idle'); // idle | restarting | deferred | done | error
      const [err, setErr] = React.useState('');
      const prevBusyWaitRef = React.useRef(false);
      const aliveRef = React.useRef(true);
      const onRestartedRef = React.useRef(props && props.onRestarted);
      onRestartedRef.current = props && props.onRestarted;

      const refreshStatus = React.useCallback(async () => {
        try {
          const res = await window.fetch('/wf-plugin/workbench/status', { headers: { accept: 'application/json' } });
          if (!res.ok) return;
          const data = await res.json();
          if (!aliveRef.current || !data || typeof data !== 'object') return;
          setStatus(data);
          const waitingNow = Boolean(data.deferred || data.restarting);
          if (data.restarting) {
            setPhase('restarting');
          } else if (data.deferred) {
            setPhase('deferred');
          } else if (prevBusyWaitRef.current && data.running) {
            setPhase('done');
            if (typeof onRestartedRef.current === 'function') onRestartedRef.current();
          }
          prevBusyWaitRef.current = waitingNow;
        } catch {
          // 后端瞬时重拉期间探测可能短暂失败，保持现状等待下一次轮询。
        }
      }, []);

      React.useEffect(() => {
        aliveRef.current = true;
        refreshStatus();
        const interval = (phase === 'deferred' || phase === 'restarting' || status.deferred || status.restarting) ? 2500 : 12000;
        const timer = setInterval(refreshStatus, interval);
        return () => { aliveRef.current = false; clearInterval(timer); };
      }, [refreshStatus, phase, status.deferred, status.restarting]);

      React.useEffect(() => {
        if (phase !== 'done') return undefined;
        const timer = setTimeout(() => { if (aliveRef.current) setPhase('idle'); }, 3500);
        return () => clearTimeout(timer);
      }, [phase]);

      const triggerRestart = async (force) => {
        setErr('');
        setPhase('restarting');
        try {
          const res = await window.fetch('/wf-plugin/workbench/restart', {
            method: 'POST',
            headers: { 'content-type': 'application/json', accept: 'application/json' },
            body: JSON.stringify(force ? { force: true } : {}),
          });
          const data = await res.json().catch(() => ({}));
          if (!aliveRef.current) return;
          if (!res.ok || data.ok === false) {
            setPhase('error');
            setErr(data.error || '重启失败');
            return;
          }
          if (data.deferred) {
            prevBusyWaitRef.current = true;
            setStatus((cur) => ({ ...cur, busy: true, deferred: true }));
            setPhase('deferred');
            return;
          }
          prevBusyWaitRef.current = false;
          setStatus((cur) => ({ ...cur, deferred: false, restarting: false, running: true }));
          setPhase('done');
          if (typeof onRestartedRef.current === 'function') onRestartedRef.current();
        } catch (e) {
          if (!aliveRef.current) return;
          setPhase('error');
          setErr(String((e && e.message) || e || '重启请求失败'));
        }
      };

      const isWaiting = phase === 'deferred' || status.deferred;
      const isRestarting = phase === 'restarting' || status.restarting;
      return h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0 } },
        isWaiting ? h('span', {
          style: { ...T.pill, fontSize: 11, color: 'var(--dsw-alias-label-secondary)' },
          title: status.deferredReason ? ('待重启原因：' + status.deferredReason) : '任务空闲后将在 5 秒内自动重启',
        }, '空闲后自动重启') : null,
        phase === 'done' ? h('span', {
          style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', whiteSpace: 'nowrap' },
        }, '✓ 后端已重启') : null,
        phase === 'error' && err ? h('span', {
          style: { fontSize: 11.5, color: 'var(--dsw-alias-state-error-primary)', whiteSpace: 'nowrap' },
          title: err,
        }, err) : null,
        isWaiting ? h('button', {
          type: 'button',
          className: 'wf-btn',
          style: { ...T.miniBtn, whiteSpace: 'nowrap' },
          disabled: isRestarting,
          title: '立即强制重启后端进程（会中断在途推进）',
          onClick: () => triggerRestart(true),
        }, '立即重启') : null,
        h('button', {
          type: 'button',
          className: 'wf-btn',
          style: { ...T.miniBtn, whiteSpace: 'nowrap' },
          disabled: isRestarting,
          title: '重启工作台后端（有任务在跑时默认等空闲后自动重启）',
          onClick: () => triggerRestart(false),
        }, isRestarting ? '正在重启…' : '重启后端'),
      );
    }

    // ======================================================================
    // 上传试卷视图
    // ======================================================================
    const MATERIAL_ROLE_OPTIONS = [
      { value: 'auto', label: '自动识别' },
      { value: 'paper', label: '试卷正文' },
      { value: 'answers', label: '答案解析' },
      { value: 'listening_script', label: '听力材料' },
      { value: 'mix', label: '整卷/组合卷' },
      { value: 'structure_reference', label: '排版参考' },
      { value: 'other', label: '其他材料' },
    ];
    const MATERIAL_ROLE_LABELS = Object.fromEntries(MATERIAL_ROLE_OPTIONS.map((o) => [o.value, o.label]));
    MATERIAL_ROLE_LABELS.explanations = '答案解析';
    function isAudioFilename(name) {
      return /\.(mp3|wav|m4a|ogg|flac|aac)$/i.test(String(name || ''));
    }
    function isDocFilename(name) {
      return /\.(docx|pdf|doc)$/i.test(String(name || ''));
    }
    function predictUploadRole(filename) {
      const stem = String(filename || '').replace(/\.[^.]+$/, '');
      if (/含答案|带答案|教师版|带解析|组合卷/i.test(stem)) return 'mix';
      if (/听力(?:录音)?(?:材料|原文|文?稿)|录音(?:材料|原文|稿)|tapescript|listening[\s_-]*script/i.test(stem)) return 'listening_script';
      if (/答案|解析|详解|answer[\s_-]*key|\banswers?\b|explanations?/i.test(stem)) return 'answers';
      if (/排版参考|版式参考|扫描版/i.test(stem)) return 'structure_reference';
      if (/正文|原卷|试卷|试题|学生版|空白卷/i.test(stem)) return 'paper';
      return 'auto';
    }

    const SHARED_BATCHES_CACHE = { lite: null, full: null };

    function UploadView(props) {
      const React = require('react');
      const h = React.createElement;
      const isActive = props.isActive !== false;
      const [form, setForm] = React.useState({ title: '', year: '', region: '安徽', subject: 'english', source_type: '', grade: '' });
      const [files, setFiles] = React.useState([]);
      const [fileRoles, setFileRoles] = React.useState([]);
      const [taxonomy, setTaxonomy] = React.useState({ sources: [], years: [], regions: [] });
      const [busy, setBusy] = React.useState(false);
      const [err, setErr] = React.useState('');
      const [notice, setNotice] = React.useState('');
      const [batches, setBatches] = React.useState(() => (typeof SHARED_BATCHES_CACHE !== 'undefined' && (SHARED_BATCHES_CACHE.full || SHARED_BATCHES_CACHE.lite)) || []);
      const [sel, setSel] = React.useState('');
      const [tick, setTick] = React.useState(0);
      const [dragging, setDragging] = React.useState(false);
      const [advancing, setAdvancing] = React.useState('');
      const fileInputRef = React.useRef(null);
      const aliveRef = React.useRef(true);
      const refreshSequence = React.useRef(0);
      const [refreshError, setRefreshError] = React.useState('');
      const [delBatch, setDelBatch] = React.useState(null); // 继续处理区删除确认弹层 {name,value,busy,error}
      const [advanceFeedback, setAdvanceFeedback] = React.useState({});
      const advanceRequests = React.useRef(new Set());
      const prevQueueRef = React.useRef([]);
      const exitTimersRef = React.useRef([]);
      const [exitingRows, setExitingRows] = React.useState([]);
      const [promotedRanks, setPromotedRanks] = React.useState({});
      React.useEffect(() => () => {
        aliveRef.current = false;
        exitTimersRef.current.forEach((id) => clearTimeout(id));
        exitTimersRef.current = [];
      }, []);

      // 排队区严格按上传顺序排序，保证前一份完成后余下顺位绝不错乱。
      const sortQueueBatches = React.useCallback((list) => {
        return (list || []).slice().sort((a, b) => {
          const ta = Number(a && a.queue_order_ts) || Date.parse((a && a.created_at) || '') || 0;
          const tb = Number(b && b.queue_order_ts) || Date.parse((b && b.created_at) || '') || 0;
          if (ta !== tb) return ta - tb;
          const ca = String((a && a.created_at) || '');
          const cb = String((b && b.created_at) || '');
          if (ca !== cb) return ca < cb ? -1 : 1;
          return String((a && a.name) || '').localeCompare(String((b && b.name) || ''));
        });
      }, []);

      // 轮询批次进度（排队区 + 流程图共用）。使用轻量列表接口避免每 2 秒传输全量题目行。
      const refresh = React.useCallback(async () => {
        const sequence = ++refreshSequence.current;
        try {
          const d = await jget('/batches?lite=1');
          if (!aliveRef.current || sequence !== refreshSequence.current) return null;
          setRefreshError('');
          const list = d.batches || [];
          if (typeof SHARED_BATCHES_CACHE !== 'undefined') SHARED_BATCHES_CACHE.lite = list;
          setBatches(list);
          // 排队区只展示「处理中」的试卷（内容审核与上传格式检查结束的已流转到试卷审核）。
          // 当前选中的那份一旦结束，自动切到按上传顺序排在第一位的下一份，让右侧流程图
          // 立即接上新首位试卷，而不是停在已完成的旧卷上。
          setSel((cur) => {
            const pending = sortQueueBatches(list.filter((b) => !b.flow_done && !b.uploaded && !b.queue_removed));
            if (!pending.length) return '';
            return pending.some((b) => b.name === cur) ? cur : pending[0].name;
          });
          return list;
        } catch (e) {
          if (aliveRef.current && sequence === refreshSequence.current) setRefreshError('进度刷新失败，当前为上次结果：' + String(e.message || e));
          return null;
        }
      }, [sortQueueBatches]);
      React.useEffect(() => {
        (async () => {
          try { const d = await jget('/taxonomy'); if (aliveRef.current) setTaxonomy({ sources: d.sources || [], years: d.years || [], regions: d.regions || [] }); }
          catch (e) { /* taxonomy is optional for the upload shell */ }
        })();
      }, []);
      React.useEffect(() => {
        if (!isActive) return;
        refresh();
        let stopped = false, timer;
        async function poll() {
          await refresh();
          if (!stopped) timer = setTimeout(poll, 2000);
        }
        timer = setTimeout(poll, 2000);
        const onFocus = () => { if (!document.hidden) refresh(); };
        window.addEventListener('focus', onFocus);
        document.addEventListener('visibilitychange', onFocus);
        return () => { stopped = true; refreshSequence.current++; clearTimeout(timer);
          window.removeEventListener('focus', onFocus); document.removeEventListener('visibilitychange', onFocus); };
      }, [refresh, isActive]);

      // 选择 / 拖放统一入口：原生 input 只保留可聚焦性与 accept，视觉完全交给自定义控件。
      function addFiles(list) {
        const incoming = Array.from(list || []);
        if (!incoming.length) return;
        const supported = /\.(docx|pdf|mp3|wav|m4a|ogg|flac|aac)$/i;
        const bad = incoming.filter((f) => !supported.test(f.name));
        // Keep every selected item visible, even equal-sized same-name files.
        // Unsupported items block submission until removed rather than vanishing.
        setFiles((prev) => prev.concat(incoming));
        setFileRoles((prev) => prev.concat(incoming.map((f) => (isDocFilename(f.name) ? 'auto' : ''))));
        setErr(bad.length ? '不支持以下文件，请移除后再上传：' + bad.map((f) => f.name).join('、') : '');
      }
      function onFiles(e) {
        addFiles(e.target.files);
        // 允许连续选择同一个文件（否则第二次不会触发 change）。
        try { e.target.value = ''; } catch (_) { /* 个别宿主不允许写，忽略 */ }
      }
      function onDrop(e) {
        e.preventDefault();
        setDragging(false);
        addFiles(e.dataTransfer && e.dataTransfer.files);
      }
      function removeFile(idx) {
        setFiles((prev) => prev.filter((_, i) => i !== idx));
        setFileRoles((prev) => prev.filter((_, i) => i !== idx));
      }
      function setFileRole(idx, role) {
        setFileRoles((prev) => prev.map((r, i) => (i === idx ? role : r)));
      }
      // 从当前阶段继续推进：宿主重启会丢掉后台推进线程，必须有一个可手动续跑
      // 的入口，否则批次会永远停在中间阶段。
      async function advance(name) {
        if (!name || advanceRequests.current.has(name)) return;
        advanceRequests.current.add(name);
        setAdvancing(name);
        setAdvanceFeedback((prev) => ({ ...prev, [name]: { message: '正在提交继续处理请求…' } }));
        try {
          const receipt = await requestAdvance(name, jpost, jget);
          if (!aliveRef.current) return;
          const feedback = advanceReceiptFeedback(receipt);
          setAdvanceFeedback((prev) => ({ ...prev, [name]: feedback }));
          // 队列并不等于结束；后续进度交给已有的统一轮询，避免另起20分钟等待循环。
          setBatches((prev) => prev.map((batch) => batch.name === name
            ? { ...batch, advance: { ...(batch.advance || {}), ...receipt } } : batch));
          await refresh();
        } catch (e) {
          if (aliveRef.current) setAdvanceFeedback((prev) => ({ ...prev, [name]: {
            error: true, message: '继续处理失败：' + String(e.message || e),
          } }));
        } finally {
          advanceRequests.current.delete(name);
          if (aliveRef.current) setAdvancing((current) => current === name ? '' : current);
        }
      }
      // 删除（继续处理区）：先请求在阶段边界停下当前运行/排队的批次，再彻底删除
      // 目录并清空本地缓存（后端 delete 端点串起 stop + purge）。确认需输入完整批次名。
      function openDel(name) { setDelBatch({ name, value: '', busy: false, error: '' }); }
      async function confirmDel() {
        if (!delBatch || delBatch.busy) return;
        if (delBatch.value !== delBatch.name) { setDelBatch({ ...delBatch, error: '请输入完整批次名以确认删除' }); return; }
        setDelBatch({ ...delBatch, busy: true, error: '' });
        try {
          await jpost('/batches/' + encodeURIComponent(delBatch.name) + '/delete', { confirm_name: delBatch.name });
          if (aliveRef.current) { setDelBatch(null); await refresh(); }
        } catch (e) {
          if (aliveRef.current) setDelBatch({ ...delBatch, busy: false, error: String((e && e.message) || e) });
        }
      }
      const delDialog = delBatch ? h('div', {
        style: { position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(16,24,40,.42)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 },
        onClick: (e) => { if (e.target === e.currentTarget && !delBatch.busy) setDelBatch(null); },
      },
        h('div', {
          role: 'dialog', 'aria-modal': 'true', 'aria-label': '删除批次确认',
          style: { width: '100%', maxWidth: 430, background: 'var(--dsw-alias-bg-layer-1)', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 14, padding: 18, boxShadow: '0 18px 48px rgba(16,24,40,.28)', display: 'flex', flexDirection: 'column', gap: 10 },
        },
          h('div', { style: { fontSize: 15, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' } }, '删除批次'),
          h('div', { style: { ...T.hint, lineHeight: 1.6 } }, '将先停止批次「' + delBatch.name + '」当前运行的任务，再永久删除它的目录、收件箱副本与本地回收归档，并清空相关缓存，无法恢复。请输入完整批次名确认：'),
          h('div', { style: { display: 'flex', gap: 8, alignItems: 'stretch' } },
            h('input', {
              className: 'wf-input', autoFocus: true, value: delBatch.value,
              style: { ...T.input, flex: 1, minWidth: 0 },
              placeholder: delBatch.name,
              onChange: (e) => setDelBatch({ ...delBatch, value: e.target.value, error: '' }),
              onKeyDown: (e) => { if (e.key === 'Enter') confirmDel(); if (e.key === 'Escape' && !delBatch.busy) setDelBatch(null); },
            }),
            h('button', {
              type: 'button', className: 'wf-btn', style: { ...T.btn, flexShrink: 0, whiteSpace: 'nowrap' },
              disabled: delBatch.busy,
              title: '一键填入完整批次名',
              onClick: () => setDelBatch({ ...delBatch, value: delBatch.name, error: '' }),
            }, '一键填入'),
          ),
          delBatch.error ? h('div', { role: 'alert', style: { ...T.statusErr, padding: '6px 9px', borderRadius: 8 } }, delBatch.error) : null,
          h('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 } },
            h('button', { className: 'wf-btn', style: T.btn, disabled: delBatch.busy, onClick: () => setDelBatch(null) }, '取消'),
            h('button', { className: 'wf-btn-danger', style: T.btnDanger, disabled: delBatch.busy || delBatch.value !== delBatch.name, onClick: confirmDel }, delBatch.busy ? '正在删除…' : '确认删除'),
          ),
        ),
      ) : null;
      async function submit() {
        if (!files.length) { setErr('请选择原卷文件（.docx 或 .pdf，可带答案与音频）'); return; }
        // 上传前拦截旧版 .doc：二进制格式无法无损解析，提示另存为 .docx。
        const docFiles = files.filter((f) => /\.doc$/i.test(f.name));
        if (docFiles.length) {
          setErr('不支持旧版 .doc：' + docFiles.map((f) => f.name).join('、') + '。请在 Word/WPS 里另存为 .docx 后再上传（.docx 保真且无需安装任何工具）。');
          return;
        }
        const unsupportedFiles = files.filter((f) => !/\.(docx|pdf|mp3|wav|m4a|ogg|flac|aac)$/i.test(f.name));
        if (unsupportedFiles.length) {
          setErr('不支持以下文件，请移除后再上传：' + unsupportedFiles.map((f) => f.name).join('、'));
          return;
        }
        setBusy(true); setErr(''); setNotice('');
        try {
          const fd = new FormData();
          fd.append('title', form.title || '');
          fd.append('year', form.year || '');
          fd.append('region', form.region || '');
          fd.append('subject', form.subject || 'english');
          fd.append('source_type', form.source_type || '');
          fd.append('grade', form.grade || '');
          const explicitRoles = {};
          files.forEach((f, idx) => {
            const chosen = fileRoles[idx];
            if (isDocFilename(f.name) && chosen && chosen !== 'auto') {
              explicitRoles[String(idx)] = chosen;
            }
          });
          if (Object.keys(explicitRoles).length) {
            fd.append('file_roles', JSON.stringify(explicitRoles));
          }
          for (const f of files) fd.append('files', f, f.name);
          const r = await window.fetch(API + '/uploads', { method: 'POST', body: fd });
          if (!r.ok) throw new Error((await r.text()) || r.status);
          const data = await r.json();
          setFiles([]);
          setFileRoles([]);
          if (fileInputRef.current) { try { fileInputRef.current.value = ''; } catch (_) {} }
          setForm({ ...form, title: '', year: '' });
          setTick((x) => x + 1);
          // 乐观立即入队：拿到上传回执就立刻把新试卷写入排队区并选中，后台再静默刷新对齐。
          if (data && data.name && !(data.duplicate && data.uploaded)) {
            const optimistic = {
              name: data.name,
              paper: data.paper || { title: form.title || data.name },
              stage: data.stage || { items: [] },
              advance: data.advance || { running: true },
              flow_done: Boolean(data.flow_done),
              uploaded: Boolean(data.uploaded),
              queue_removed: Boolean(data.queue_removed),
              created_at: data.created_at || new Date().toISOString(),
              updated_at: data.updated_at || new Date().toISOString(),
              queue_order_ts: data.queue_order_ts || (Date.now() * 1e6),
            };
            setBatches((prev) => {
              const base = Array.isArray(prev) ? prev : [];
              const idx = base.findIndex((b) => b && b.name === data.name);
              const next = idx >= 0
                ? base.map((b, i) => (i === idx ? { ...b, ...optimistic } : b))
                : base.concat([optimistic]);
              if (typeof SHARED_BATCHES_CACHE !== 'undefined') SHARED_BATCHES_CACHE.lite = next;
              return next;
            });
            setSel(data.name);
          }
          refresh();
          // 重复上传不会新建任务：明确告知，避免“批次任务不见了”的错觉。
          if (data && (data.duplicate || data.reused)) setNotice(data.message || '该试卷已存在，已定位到已有任务。');
          // 来源提示：说明本次用 Word 还是 PDF 作提取主源，以及为什么。
          else if (data && data.source_notice) setNotice(data.source_notice);
          if (props.onUploaded) props.onUploaded(data);
        } catch (e) { setErr(String(e.message || e)); }
        finally { setBusy(false); }
      }

      const inp = { ...T.input, width: '100%', padding: '6px 10px', fontSize: 12.5 };
      // 「处理中」= 内容审核与上传格式检查未结束且未上传，严格按上传顺序排序；「已完成」= 等待人工确认。
      const pendingBatches = React.useMemo(
        () => sortQueueBatches(batches.filter((b) => !b.flow_done && !b.uploaded && !b.queue_removed)),
        [batches, sortQueueBatches],
      );
      const pendingOrderKey = pendingBatches.map((b) => b.name).join('\0');
      React.useEffect(() => {
        const prev = prevQueueRef.current;
        prevQueueRef.current = pendingBatches;
        if (!prev.length) return;
        const currNames = new Set(pendingBatches.map((b) => b.name));
        const gone = [];
        prev.forEach((b, prevIdx) => {
          if (!currNames.has(b.name)) gone.push({ name: b.name, batch: b, prevIdx });
        });
        if (!gone.length) return;
        const prevIdxMap = new Map(prev.map((b, i) => [b.name, i]));
        const stamp = Date.now();
        const promoted = {};
        pendingBatches.forEach((b, newIdx) => {
          const oldIdx = prevIdxMap.get(b.name);
          if (oldIdx !== undefined && newIdx < oldIdx) promoted[b.name] = stamp;
        });
        const goneNames = new Set(gone.map((g) => g.name));
        setExitingRows((cur) => cur.filter((x) => !goneNames.has(x.name)).concat(gone));
        if (Object.keys(promoted).length) {
          setPromotedRanks((cur) => ({ ...cur, ...promoted }));
        }
        const tid = setTimeout(() => {
          if (!aliveRef.current) return;
          setExitingRows((cur) => cur.filter((x) => !goneNames.has(x.name)));
          setPromotedRanks((cur) => {
            const next = { ...cur };
            Object.keys(promoted).forEach((k) => { if (next[k] === stamp) delete next[k]; });
            return next;
          });
        }, 440);
        exitTimersRef.current.push(tid);
      }, [pendingOrderKey]);
      const finishedBatches = batches.filter((b) => b.flow_done && !b.uploaded);
      const sectionHead = (title) => h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 7 } },
        h('span', { style: T.sectionTitle }, title));

      const uptop = h('div', { style: { display: 'flex', flexDirection: 'column', flex: '0 0 auto' } },
        h('div', { style: { ...T.card, padding: '10px 12px', marginBottom: 0 } },
          sectionHead('试卷信息'),
          h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '7px 12px' } },
            h('div', { style: { gridColumn: '1 / -1' } }, formField(TEXT.uploadTitle,
              h('input', { className: 'wf-input', style: inp, value: form.title, placeholder: '留空自动识别', onChange: (e) => setForm({ ...form, title: e.target.value }) }))),
            formField(TEXT.uploadYear,
              h('input', { className: 'wf-input', style: inp, value: form.year, placeholder: '自动识别', onChange: (e) => setForm({ ...form, year: e.target.value }) })),
            formField(TEXT.uploadRegion,
              h('input', { className: 'wf-input', style: inp, value: form.region, onChange: (e) => setForm({ ...form, region: e.target.value }) })),
            formField('题源分类',
              h('select', { className: 'wf-input', style: inp, value: form.source_type, onChange: (e) => setForm({ ...form, source_type: e.target.value }) },
                h('option', { value: '' }, '未选择'),
                taxonomy.sources.map((source) => h('option', { key: source, value: source }, source)))),
            formField('年级',
              h('select', { className: 'wf-input', style: inp, value: form.grade, onChange: (e) => setForm({ ...form, grade: e.target.value }) },
                h('option', { value: '' }, '未选择'),
                ['七年级', '八年级', '九年级'].map((grade) => h('option', { key: grade, value: grade }, grade)))),
          ),
          h('div', { style: { height: 1, background: 'var(--dsw-alias-border-l1)', margin: '9px 0 9px' } }),
          sectionHead(files.length ? '原卷文件 · 已选 ' + files.length + ' 个' : '原卷文件'),
          // 文件选择：中文浅灰控件。原生按钮（Choose Files / 深色描边 / 右侧英文状态）
          // 被隐藏，这里只保留一个浅灰文字入口，无底色、无黑框、无英文。
          h('div', { style: { display: 'flex', alignItems: 'stretch', gap: 10 } },
            h('label', {
              className: 'wf-file-drop' + (dragging ? ' wf-dragover' : ''),
              style: { flex: 1, minWidth: 0, width: 'auto' },
              onDragOver: (e) => { e.preventDefault(); setDragging(true); },
              onDragLeave: (e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false); },
              onDrop,
            },
              h('input', { ref: fileInputRef, className: 'wf-file-input', type: 'file', multiple: true, accept: '.docx,.pdf,.mp3,.wav,.m4a,.ogg,.flac,.aac', onChange: onFiles, 'aria-label': '选择上传文件' }),
              h('span', { style: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, color: 'var(--dsw-alias-label-secondary)' } }, uploadIcon(18)),
              h('span', { style: { display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 } },
                h('span', { className: 'wf-file-label' }, '选择上传文件')),
            ),
            h('button', { className: 'wf-btn-primary', style: { ...T.btnPrimary, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: submit }, busy ? TEXT.uploading : '上传'),
          ),
          files.length ? h('div', { style: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6, marginTop: 10 } },
            files.map((f, i) => {
              const isAudio = isAudioFilename(f.name);
              const isDoc = isDocFilename(f.name);
              const pred = isDoc ? predictUploadRole(f.name) : 'auto';
              const curRole = fileRoles[i] || 'auto';
              return h('span', { key: f.name + ':' + i, className: 'wf-file-chip', style: { gap: 6 } },
                h('span', { title: f.name, style: { maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, f.name),
                isAudio
                  ? h('span', { style: { fontSize: 10.5, padding: '1px 5px', borderRadius: 4, background: 'var(--dsw-alias-bg-layer-2, rgba(0,0,0,0.06))', color: 'var(--dsw-alias-label-secondary)' } }, '听力音频')
                  : (isDoc ? h('select', {
                      'aria-label': f.name + ' 材料角色',
                      value: curRole,
                      onChange: (e) => setFileRole(i, e.target.value),
                      style: { fontSize: 11, padding: '1px 4px', borderRadius: 4, border: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-alias-bg-base)', color: 'var(--dsw-alias-label-primary)', cursor: 'pointer', maxWidth: 118 },
                    }, MATERIAL_ROLE_OPTIONS.map((opt) => h('option', { key: opt.value, value: opt.value },
                      opt.value === 'auto' && pred !== 'auto' ? ('自动·' + (MATERIAL_ROLE_LABELS[pred] || pred)) : opt.label))) : null),
                h('button', { type: 'button', title: '移除', 'aria-label': '移除 ' + f.name, onClick: () => removeFile(i) }, closeIcon()),
              );
            })) : null,
          err ? h('div', { style: { ...T.statusErr, marginTop: 10, display: 'flex', alignItems: 'flex-start', gap: 7, lineHeight: 1.6 } },
            h('span', { style: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 15, height: 15, borderRadius: '50%', flexShrink: 0, marginTop: 2, background: 'var(--dsw-alias-state-error-primary)', color: '#fff', fontSize: 10, fontWeight: 700 } }, '!'),
            h('span', null, err)) : null,
          notice ? h('div', { role: 'status', 'aria-live': 'polite', style: { ...T.statusOk, marginTop: 10, lineHeight: 1.6 } }, notice) : null,
        ),
      );

      // 合并正在退场的已完成批次与当前排队批次：已完成项原地淡出收起，后续项立即更新序号并平滑上移。
      const queueDisplayRows = (() => {
        const rows = pendingBatches.map((b, i) => ({ kind: 'live', batch: b, rank: i + 1 }));
        if (exitingRows.length) {
          const sortedExits = exitingRows.slice().sort((a, b) => a.prevIdx - b.prevIdx);
          sortedExits.forEach((ex, offset) => {
            const pos = Math.max(0, Math.min(rows.length, ex.prevIdx + offset));
            rows.splice(pos, 0, { kind: 'exiting', batch: ex.batch, rank: 0 });
          });
        }
        return rows;
      })();

      // 左下：排队区常驻显示（即使没有处理中的试卷也保留这块区域和空状态）。
      // 只列「处理中」的试卷：内容审核与上传格式检查结束的已从队列移除，流转到试卷审核等待人工确认，
      // 但它们不再占用排队区，也不改变排队区本身始终存在。
      const queue = h('div', { style: { flex: 1, minHeight: 190, display: 'flex', flexDirection: 'column', marginTop: 12 } },
        h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 } },
          h('span', { style: { fontSize: 14, fontWeight: 600 } }, '排队区'),
          h('span', { style: T.pill }, '共排队 ', h('span', { style: T.pillNum }, String(pendingBatches.length)), ' 份'),
          h('span', { style: { flex: 1 } }),
          h(BackendRestartControl, { onRestarted: () => refresh() })),
        h('div', { className: 'wf-scroll', style: { flex: 1, overflow: 'auto', minHeight: 0, border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 12, background: 'var(--dsw-alias-bg-layer-1)', padding: 6, display: queueDisplayRows.length === 0 ? 'flex' : 'block', flexDirection: 'column' } },
          queueDisplayRows.length === 0
            ? h(EmptyState, {
                title: finishedBatches.length ? '暂无处理中的试卷' : '暂无试卷',
                hint: finishedBatches.length ? '上传新试卷会开始一轮新流程。' : '上传试卷后，会在这里排队。',
                flex: true, compact: true,
              })
            : queueDisplayRows.map((entry) => {
                const b = entry.batch;
                const exiting = entry.kind === 'exiting';
                const st = b.stage || {};
                const cur = st.current || {};
                const progress = compactProgress(st);
                const done = progress.done, total = progress.total;
                const pct = exiting ? 100 : (total ? Math.round(done / total * 100) : 0);
                const active = !exiting && b.name === sel;
                const promotedStamp = !exiting ? promotedRanks[b.name] : null;
                const rowCls = 'wf-queue-row' + (exiting ? ' wf-queue-row-exiting' : (promotedStamp ? ' wf-queue-row-promoted' : ''));
                return h('div', {
                  key: (exiting ? 'exit:' : '') + b.name,
                  className: rowCls,
                  onClick: exiting ? undefined : () => setSel(b.name),
                  style: {
                    display: 'flex', alignItems: 'center', gap: 10, padding: '9px 10px', borderRadius: 9, cursor: exiting ? 'default' : 'pointer',
                    background: active ? 'var(--wf-sel-bg)' : 'transparent',
                    marginBottom: 3,
                    transition: 'background .16s',
                  },
                },
                  h('span', {
                    key: promotedStamp ? ('rank:' + entry.rank + ':' + promotedStamp) : ('rank:' + entry.rank),
                    className: promotedStamp ? 'wf-queue-rank-up' : '',
                    style: {
                      flex: '0 0 auto', width: 22, height: 22, borderRadius: 7,
                      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 11.5, fontWeight: 700,
                      background: exiting ? 'rgba(22,163,74,.16)' : 'var(--dsw-alias-bg-layer-2)',
                      color: exiting ? '#15803d' : (active ? 'var(--wf-brand)' : 'var(--dsw-alias-label-secondary)'),
                    },
                  }, exiting ? '✓' : String(entry.rank)),
                  h('div', { style: { flex: 1, minWidth: 0 } },
                    h('div', { style: { fontSize: 12.5, fontWeight: 600, color: exiting ? '#15803d' : (active ? 'var(--wf-brand)' : 'var(--dsw-alias-label-primary)'), overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, (b.paper && b.paper.title) || b.name),
                    h('div', { style: { ...T.hint, fontSize: 11, marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, exiting ? '已完成，已移出排队区' : ((cur.id ? shortStageName(cur) : (st.hint || '等待')) + (total ? ' · ' + done + '/' + total : ''))),
                    h('div', { style: { height: 3, borderRadius: 999, background: 'var(--dsw-alias-bg-layer-2)', marginTop: 6, overflow: 'hidden' } },
                      h('div', { style: { width: pct + '%', height: '100%', borderRadius: 999, background: exiting ? '#16a34a' : 'var(--wf-brand)', opacity: .75, transition: 'width .3s ease' } })),
                  ),
                  h('span', { style: { flex: '0 0 auto', fontSize: 11, color: exiting ? '#15803d' : 'var(--dsw-alias-label-secondary)', fontVariantNumeric: 'tabular-nums' } }, pct + '%'),
                );
              }),
        ),
      );



      // 右侧：12 类紧凑依赖图。判型后四路并行，听力只在内容审核汇合。
      // 整图随容器等比缩放；沿用现有主题与中性完成态，不展示常驻内部步骤。
      const selBatch = pendingBatches.find((b) => b.name === sel) || pendingBatches[0];
      const stage = (selBatch && selBatch.stage) || {};
      const sourceItems = stage.items || [];
      const isFlowOverview = !selBatch;
      const items = compactFlowStages(sourceItems, isFlowOverview);
      // “当前”可能有多个：并行阶段同时 active 时，当前环节集合就是它们对应的展示组，
      // 不再只指向单个 current，避免并行时节点轮流闪成唯一高亮。
      const curIds = new Set(items.filter((it) => it.inProgress).map((it) => it.id));
      const DONE = { completed: true, approved: true, skipped: true, completed_with_findings: true };
      const doneN = items.filter((it) => DONE[it.status]).length;
      // 简单蛇形布局：每行 3 个、共 4 行，节点按顺序首尾相接成一条线。
      // 不再画多条依赖分支线，只用一条折线把 12 个节点串起来（行末折回下一行）。
      // 尺寸整体收缩：更小的列宽/行距/边距，整图在容器里更紧凑。
      const SHOW = items.slice(0, 12);
      const COLS = 3;
      const R = 11, CW = 84, ROW_H = 86, PADX = 34, PADY = 22;
      const colX = (c) => PADX + c * CW;
      const rowY = (r) => PADY + r * ROW_H;
      const grid = SHOW.map((it, i) => {
        const row = Math.floor(i / COLS), col = i % COLS;
        // 偶数行从左到右，奇数行从右到左，相邻节点首尾折返成蛇形。
        const snakeCol = row % 2 === 0 ? col : COLS - 1 - col;
        return { it, i, row, col: snakeCol, x: colX(snakeCol), y: rowY(row) };
      });
      const svgW = PADX * 2 + (COLS - 1) * CW;
      const svgH = PADY * 2 + 3 * ROW_H + 58;
      // 节点名拆成最多两行：按中西文宽度估算，整词（中文按字）换行，超出才省略。
      function flowLabelLines(name) {
        const chars = Array.from(String(name == null ? '' : name).trim());
        const lines = [];
        let buf = '', width = 0, used = 0;
        for (let i = 0; i < chars.length; i++) {
          const ch = chars[i];
          const cw = /[\x20-\x7e]/.test(ch) ? 0.55 : 1;
          if (width + cw > 9 && buf) {
            lines.push(buf);
            buf = ''; width = 0;
            if (lines.length === 2) { used = i; break; }
          }
          buf += ch; width += cw; used = i + 1;
        }
        if (buf && lines.length < 2) lines.push(buf);
        if (used < chars.length && lines.length) {
          const last = lines[lines.length - 1];
          lines[lines.length - 1] = (last.length > 1 ? last.slice(0, -1) : last) + '…';
        }
        return lines.length ? lines : ['—'];
      }
      const todoLine = 'var(--wf-flow-line)';
      const doneLine = 'var(--wf-flow-line-done)';
      // 一条首尾相连的线：相邻节点之间连一段；同一行走水平直线穿到下一个节点，
      // 换行时在行末向下折、再沿下一行方向进入首节点。整条线共享一个“已完成”判定，
      // 只有当两端节点都已完成时该段才显示为已走过颜色。
      const segs = [], arrows = [];
      const lineFor = (a, b, reached) => {
        if (a.row === b.row) {
          const x1 = a.x + (b.x > a.x ? R : -R), x2 = b.x + (b.x > a.x ? -R : R);
          return { d: 'M' + x1 + ',' + a.y + ' L' + x2 + ',' + b.y, end: [x2, b.y], dir: b.x > a.x ? 'right' : 'left' };
        }
        // 换行折返：从行末节点向下，到下一行高度再水平进入下一行首节点。
        const dirX = b.x > a.x ? 1 : -1;
        const midY = (a.y + b.y) / 2 + (b.y > a.y ? -6 : 6);
        const x1 = a.x, y1 = a.y + (b.y > a.y ? R : -R);
        const x2 = b.x, y2 = b.y + (b.y > a.y ? -R : R);
        return { d: 'M' + x1 + ',' + y1 + ' L' + x1 + ',' + midY + ' L' + x2 + ',' + midY + ' L' + x2 + ',' + y2, end: [x2, y2], dir: 'down' };
      };
      for (let k = 0; k < grid.length - 1; k += 1) {
        const a = grid[k], b = grid[k + 1];
        const reached = !!DONE[a.it.status] && !!DONE[b.it.status];
        const live = !!a.it.inProgress || !!b.it.inProgress;
        const segLine = reached ? doneLine : live ? 'var(--wf-flow-line-live)' : todoLine;
        const seg = lineFor(a, b, reached);
        segs.push({ from: a.it.id, to: b.it.id, d: seg.d, reached, live, line: segLine });
        arrows.push({ x: seg.end[0], y: seg.end[1], dir: seg.dir, reached, live, line: segLine });
      }
      const nodes = grid;
      const flowFeedback = selBatch ? (advancing === selBatch.name
        ? { message: '正在提交继续处理请求…' }
        : advanceProgressFeedback(selBatch.advance, advanceFeedback[selBatch.name])) : { message: '' };
      const flowHead = h('div', { style: { flexShrink: 0 } },
        h('div', { style: { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' } },
          h('div', { style: { minWidth: 0, flex: 1 } },
            h('div', { style: { fontSize: 14, fontWeight: 600, letterSpacing: '-.01em' } }, isFlowOverview ? '流程总览' : '流程进度'),
            selBatch
              ? h('div', { style: { fontSize: 12, color: 'var(--dsw-alias-label-primary)', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, (selBatch.paper && selBatch.paper.title) || selBatch.name)
              : null),
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0, flexWrap: 'wrap', justifyContent: 'flex-end', paddingTop: 2 } },
            selBatch ? (() => { const queued = (selBatch.advance || {}).queued || (selBatch.advance || {}).waiting; const busy = advancing === selBatch.name || (selBatch.advance || {}).running || queued; const label = advancing === selBatch.name ? '提交中…' : ((selBatch.advance || {}).running ? '推进中…' : (queued ? ((selBatch.advance || {}).waiting ? '等待前一份…' : '排队中…') : '继续处理')); return h('button', { className: 'wf-btn-primary', style: { ...T.miniBtnPrimary, padding: '4px 12px' }, disabled: busy, title: '从当前阶段继续推进', onClick: () => advance(selBatch.name) }, label); })() : null),
            selBatch ? h('button', { className: 'wf-btn-danger', style: { ...T.btnDanger, padding: '4px 12px', color: 'var(--dsw-alias-state-error-primary)' }, title: '停止并彻底删除该批次（清空本地缓存）', onClick: () => openDel(selBatch.name) }, '删除') : null,
        ),
      );
      // 整图是一个自带 viewBox 的 SVG：随右栏宽度等比缩放，绝不会顶出卡片边界。
      // 悬停浮层里的子步骤状态：只显示“名称·状态”，保持极短，不显示长描述。
      function compactStatusLabel(status) {
        const map = { completed: '已完成', completed_with_findings: '有差异', approved: '已批准',
          skipped: '已跳过', failed: '失败', blocked: '受阻', needs_review: '待核验',
          invalidated: '需重跑', pending: '待处理', running: '处理中', warning: '提示' };
        return map[status] || statusText(status);
      }
      // 演示态没有真实子步骤时，按分类列出它包含的后台子流程名，供悬浮层说明。
      function flowOverviewSubsteps(it) {
        const names = (it.stage_ids || []).flatMap((id) => id === 'explanations'
          ? ['解析与大意填充', '听力录音材料直抄'] : [shortStageName({ id })]);
        return (names.length ? names : [shortStageName(it)]).map((name) => '· ' + name);
      }
      const flowDiagram = h('div', { className: 'wf-flow-canvas', style: { position: 'absolute', inset: '10px 8px 4px', display: 'flex', justifyContent: 'center', alignItems: 'center' } },
        h('svg', {
          viewBox: '0 0 ' + svgW + ' ' + svgH,
          role: 'group',
          // 双向自适应容器：宽高 100% 占满可视区，由 preserveAspectRatio 按 viewBox 等比完整容纳，
          // 面板变宽或变窄都不会撑高出纵向滚动条，始终一屏看全 12 个环节。
          preserveAspectRatio: 'xMidYMid meet',
          style: { display: 'block', width: '100%', height: '100%', maxWidth: '100%', maxHeight: '100%' },
          'aria-label': isFlowOverview ? '试卷处理流程总览：' + items.length + ' 个环节' : '试卷处理流程：' + items.length + ' 个阶段，已完成 ' + doneN + ' 个',
        },
          segs.map((s, si) => h('path', {
            key: 'p' + si, 'data-flow-from': s.from, 'data-flow-to': s.to, d: s.d, fill: 'none',
            stroke: s.line,
            strokeWidth: s.reached ? 2.2 : 1.8,
            strokeOpacity: s.reached ? .6 : 1,
            // 运行中的连线走流动虚线；已走过与未走保持原样。
            strokeDasharray: s.live ? '6 5' : null,
            className: s.live ? 'wf-flow-live' : null,
            strokeLinecap: 'round', strokeLinejoin: 'round',
          })),
          arrows.map((ar, ai) => {
            const s = 3.2;
            const d = ar.dir === 'right' ? 'M' + (ar.x - s) + ',' + (ar.y - s) + ' L' + (ar.x + s) + ',' + ar.y + ' L' + (ar.x - s) + ',' + (ar.y + s)
              : ar.dir === 'left' ? 'M' + (ar.x + s) + ',' + (ar.y - s) + ' L' + (ar.x - s) + ',' + ar.y + ' L' + (ar.x + s) + ',' + (ar.y + s)
                : 'M' + (ar.x - s) + ',' + (ar.y - s) + ' L' + ar.x + ',' + (ar.y + s) + ' L' + (ar.x + s) + ',' + (ar.y - s);
            return h('path', { key: 'a' + ai, d, fill: ar.line, stroke: 'none' });
          }),
          nodes.map((n) => {
            const it = n.it;
            const isCur = curIds.has(it.id);
            const done = !!DONE[it.status];
            const failed = it.status === 'blocked' || it.status === 'failed';
            const warn = it.status === 'needs_review' || it.status === 'invalidated' || it.status === 'completed_with_findings';
            // 深层中性色 + 状态只靠描边 / 对勾 / 中心点区分：
            // 已完成=实底浅灰 + 勾；未运行=浅空心；运行中=脉冲圈 + 呼吸点；失败/差异=彩色描边 + !
            const fillTone = done && !failed && !warn ? 'var(--wf-flow-node-done)'
              : failed || warn ? 'var(--wf-flow-node-alert)' : 'var(--wf-flow-node-idle)';
            const ring = failed ? 'var(--wf-flow-node-error)'
              : warn ? 'var(--wf-flow-node-warn)'
                : done ? 'var(--wf-flow-node-done-ring)'
                  : isCur ? 'var(--wf-flow-node-cur-ring)' : 'var(--wf-flow-node-ring)';
            const lines = flowLabelLines(shortStageName(it));
            // 悬浮/键盘读名要展开子流程：先给分类与状态，再逐条列出子步骤「名称·状态」。
            // 演示态（无任务）没有真实子步骤，给出各分类包含的子流程名作说明。
            const nodeLabel = isFlowOverview
              ? (shortStageName(it) + '\n' + flowOverviewSubsteps(it).join('\n'))
              : [shortStageName(it) + ' · ' + statusText(it.status)]
                .concat((it.substeps || []).map((m) => shortStageName(m) + ' · ' + compactStatusLabel(m.status)))
                .join('\n');
            return h('g', { key: it.id, role: 'img', tabIndex: 0, 'aria-label': nodeLabel, 'data-stage-ids': it.stage_ids.join(' '), 'data-status': it.status },
              // 运行中：脉冲外圈持续放大淡出，叠在实心环外侧。
              isCur ? h('circle', { className: 'wf-node-pulse', cx: n.x, cy: n.y, r: R, fill: 'none', stroke: 'var(--wf-flow-node-cur-ring)', strokeWidth: 1.4 }) : null,
              // 悬停和键盘读名共用真实短状态，不含长描述/备注。
              h('title', null, nodeLabel),
              h('circle', {
                cx: n.x, cy: n.y, r: R,
                fill: fillTone,
                stroke: ring, strokeWidth: 1.6,
              }),
              done && !warn ? h('text', { x: n.x, y: n.y + 4, textAnchor: 'middle', fontSize: 11, fontWeight: 700, fill: 'var(--wf-flow-node-done-mark)' }, '✓')
                : failed ? h('text', { x: n.x, y: n.y + 4, textAnchor: 'middle', fontSize: 11, fontWeight: 700, fill: 'var(--wf-flow-node-error)' }, '!')
                  : warn ? h('text', { x: n.x, y: n.y + 4, textAnchor: 'middle', fontSize: 11, fontWeight: 700, fill: 'var(--wf-flow-node-warn)' }, '!')
                    : isCur ? h('circle', { className: 'wf-node-dot', cx: n.x, cy: n.y, r: 3.6, fill: 'var(--wf-flow-node-cur-mark)' }) : null,
              // 节点名：两行居中；字号贴近正文（约 12px），与其他文字大小相近。
              lines.map((line, li) => h('text', {
                key: 'l' + li,
                x: n.x, y: n.y + R + 15 + li * 14,
                textAnchor: 'middle', fontSize: 11.5,
                fontWeight: isCur ? 700 : 500,
                fill: isCur ? 'var(--wf-brand)' : (done ? 'var(--dsw-alias-label-primary)' : 'var(--dsw-alias-label-secondary)'),
              }, line)),
            );
          }),
        ));
      const flow = h('div', { className: 'wf-upload-right', style: { minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 12 } },
        h('div', { className: 'wf-flow-card', style: { ...T.card, position: 'relative', paddingBottom: 12, flex: 1, minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column', marginBottom: 0 } },
          flowHead,
          refreshError ? h('p', { role: 'alert', style: { ...T.statusErr, margin: '5px 0' } }, refreshError) : null,
          h('div', { className: 'wf-flow-stage', style: { flex: 1, minWidth: 0, minHeight: 0, position: 'relative', overflow: 'hidden', display: 'flex', justifyContent: 'center', alignItems: 'center' } },
            items.length === 0
              ? h('div', { style: { width: '100%', display: 'flex', flex: 1 } }, h(EmptyState, { title: '暂无流程', flex: true }))
              : flowDiagram),
          diagBanner(selBatch, stage, refresh, flowFeedback),
        ),
        h(OperationsPanel, { key: 'operations:' + (selBatch ? selBatch.name : ''), batchName: selBatch ? selBatch.name : '' }),
      );

      return h('div', { className: 'wf-upload-layout' },
        h('div', { className: 'wf-upload-columns' },
          // 左列：上（上传）+ 下（排队区，只列处理中）
          h('div', { className: 'wf-upload-left wf-scroll' }, uptop, queue),
          // 右列：流程进度
          flow,
        ),
        delDialog,
      );
    }
    // AI 运维是附加观察面板；读取记录不启动接手，也不替代原有自愈。
    function operationsText(value) {
      if (value == null) return '';
      if (typeof value !== 'object') return String(value);
      try { return JSON.stringify(value); } catch (_) { return '无法读取详情'; }
    }
    function operationsView(incident, context = {}) {
      const item = incident && typeof incident === 'object' && !Array.isArray(incident) ? incident : {};
      const events = Array.isArray(item.events) ? item.events.filter((e) => e && typeof e === 'object') : [];
      const timeOf = (e) => e.timestamp || e.created_at || e.at || e.time || '';
      const latest = events.reduce((last, event) => {
        if (!last) return event;
        const a = Date.parse(timeOf(last)), b = Date.parse(timeOf(event));
        return Number.isFinite(a) && Number.isFinite(b) && b < a ? last : event;
      }, null) || {};
      // Heartbeats prove liveness; keep the last diagnostic or repair action visible.
      const substantive = events.filter(e => !['heartbeat', 'progress'].includes(e.action));
      const business = substantive.reduce((last, event) => {
        if (!last) return event;
        const a = Date.parse(timeOf(last)), b = Date.parse(timeOf(event));
        return Number.isFinite(a) && Number.isFinite(b) && b < a ? last : event;
      }, null) || latest;
      const verification = item.verification || latest.verification || null;
      // Only the backend's structured verification may establish recovery; prose cannot.
      const verified = !!(verification && typeof verification === 'object'
        && (verification.verified === true || verification.passed === true)
        && typeof verification.evidence_id === 'string' && verification.evidence_id.trim());
      const raw = operationsText(item.status).toLowerCase();
      const pipelineProgress = item.resolution_source === 'pipeline_progress'
        || item.resolved_by_operations === false;
      const labels = {
        detected: '已触发', unclaimed: '已触发', idle: '已触发', pending: '已触发', queued: '已触发',
        investigating: '调查中', investigation: '调查中', diagnosing: '调查中',
        repairing: '修复中', repair: '修复中', verifying: '复验中', verification: '复验中',
        recovered: '已恢复', resolved: pipelineProgress ? '流水线已复验' : '已恢复', removed: '已移出排队区', waiting_external: '待外部条件', awaiting_external: '待外部条件',
        awaiting_execution: '排队', needs_deep_repair: '自动升级修复', needs_human: '自动升级修复', recovery_queued: '自动升级修复', waiting_evidence: '等待新证据', claimed: '调查中', working: '调查中',
        blocked: '待外部条件', cancelled: '已取消', canceled: '已取消',
      };
      let label = !incident ? '未接手' : (labels[raw] || '状态待确认');
      if (raw === 'claimed' || raw === 'working') {
        if (latest.action === 'apply_repair' || latest.action === 'preview_repair') label = '修复中';
        if (latest.action === 'verify') label = '复验中';
      }
      if ((raw === 'recovered' || raw === 'resolved') && pipelineProgress) label = '流水线已复验';
      else if ((raw === 'recovered' || raw === 'resolved') && !verified) label = '复验待确认';
      const unknown = (context.receipts || []).filter(r => r && r.status === 'unknown').length;
      const now = context.now || Date.now();
      const hostAttempts = ((context.host || {}).attempts || []).filter(r =>
        r.batch_name === context.batchName && r.incident_id === item.incident_id);
      const hostAttempt = hostAttempts.slice().reverse().find(r => r.active) || hostAttempts[hostAttempts.length - 1];
      if (raw === 'claimed' || raw === 'working') {
        if (['apply_repair', 'preview_repair', 'regenerate_units', 'request_code_repair', 'code_repair', 'install_code_repair'].includes(business.action)) label = '修复中';
        if (business.action === 'verify') label = '复验中';
        if (hostAttempt && hostAttempt.active) {
          const phases = { starting: '已触发', investigating: '调查中', diagnosing: '调查中', waiting_repair: '等待修复锁', repairing: '修复中', verifying: '复验中', awaiting_execution: '等待原流程续跑',
            awaiting_restart: '等待重启复验', session_failed: '会话失败', backend_disconnected: '连接中断',
            lease_lost: '租约失效', budget_exceeded: '本次预算已用尽', outcome_unknown: '待核对动作回执',
            waiting_worker: '等待批次执行锁释放', release_deferred: '释放待接续' };
          label = phases[hostAttempt.state] || label;
        }
        if (item.lease_expires_at && item.lease_expires_at * 1000 <= now) label = '接手已过期';
      }
      const mode = hostAttempt && hostAttempt.mode || item.mode;
      if (['claimed', 'working', 'investigating', 'diagnosing'].includes(raw) && mode === 'investigation' && ['调查中', '修复中', '复验中'].includes(label)) label = '调查中';
      if (['claimed', 'working'].includes(raw) && mode === 'repair') {
        const phases = { model_wait: '准备修复', evidence_read: '定位代码', code_write: '修改中',
          code_test: '测试中', code_prepare: '准备安装', code_install: '安装中',
          verifying: '复验中', no_substantive_progress: '修复无进展' };
        const phase = hostAttempt && hostAttempt.phase || (item.repair_progress || {}).phase;
        if (phases[phase] && ['调查中', '准备修复', '修复中', '复验中'].includes(label)) label = phases[phase];
        else if (label === '调查中') label = '准备修复';
      }
      if (raw === 'awaiting_execution') label = '等待原流程续跑';
      if (raw === 'queued' && item.not_before * 1000 > now) label = '冷却等待';
      if (unknown) label = '待核对动作回执';
      if (hostAttempt && hostAttempt.state === 'configuration_failed') label = '模型配置受阻';
      return { label, raw, verified, unknown,
        heartbeat: operationsText(timeOf(latest)), retryAt: item.not_before ? new Date(item.not_before * 1000).toLocaleString() : '',
        error: hostAttempt && hostAttempt.error || '', sessionId: typeof item.session_id === 'string' ? item.session_id : '',
        time: operationsText(timeOf(business) || item.updated_at),
        action: operationsText(business.action || business.type || business.event || business.kind),
        outcome: [operationsText(business.outcome || business.result || business.message), operationsText(business.detail)].filter(Boolean).join(' · '),
        verification: operationsText(verification),
      };
    }
    function operationsSummary(host, record, options = {}) {
      const batchName = options.batchName || '';
      const current = record && record.batchName === batchName ? record : null;
      const attempts = Array.isArray(host.attempts) ? host.attempts : [];
      const active = attempts.slice().reverse().find((a) => a && a.active && (!batchName || a.batch_name === batchName));
      const incident = current && current.incident || (!batchName && active ? { incident_id: active.incident_id, status: 'claimed', mode: active.mode } : null);
      const selectedBatch = batchName || active && active.batch_name || '';
      const view = operationsView(incident, { host, batchName: selectedBatch, receipts: current && current.receipts || [], now: options.now });
      const scope = selectedBatch ? '批次「' + selectedBatch + '」（workflows/' + selectedBatch + '）' : '';
      const sentence = (text) => {
        const detail = operationsText(text).replace(/\s+/g, ' ').trim();
        return detail ? [scope, detail].filter(Boolean).join('：') : '';
      };
      const fail = (label, text) => ({ label, hint: sentence(text), error: true });
      if (!host.loaded) return options.hostError ? fail('服务失联', options.hostError) : { label: '状态读取中', hint: '', error: false };
      if (!host.enabled) return { label: '已关闭', hint: '', error: false };
      if (!host.available) return fail('服务不可用', options.hostError || host.configError || 'Harness 运维服务不可用');
      if (options.hostError || host.configError) return fail('配置受阻', options.hostError || host.configError);
      if (host.lastPollError) return fail('扫描异常', host.lastPollError);
      if (host.running === false || host.loopAlive === false) return fail('监测已停止', '运维扫描循环未运行');
      const scannedAt = Date.parse(host.lastPollAt), now = options.now || Date.now();
      if (host.pollStale || Number.isFinite(scannedAt) && now - scannedAt > 20000) return fail('服务失联', '超过 20 秒未成功扫描，正在重试');
      if (options.recordError) return fail('记录不可用', '运维记录读取失败：' + options.recordError);
      if (batchName && !current) return { label: '记录读取中', hint: '', error: false };
      if (view.error) return fail(view.label, view.error);
      if (view.unknown) return fail(view.label, view.unknown + ' 个动作结果未确认，等待核对回执');
      if (incident) {
        const triggerLabels = {
          stale_running: '检测到僵死运行阶段',
          repeated_transient: '连续瞬态失败自动升级',
          manual_takeover: '人工触发接手',
          first_blocker: '首次阻断自动接手',
          failed_self_heal: '自愈失败升级接手',
        };
        const parts = [];
        const trig = triggerLabels[incident.trigger];
        if (trig) parts.push(trig);
        const waitSec = incident.status === 'queued' && Number.isFinite(Number(incident.not_before))
          ? Math.ceil(Number(incident.not_before) - now / 1000) : 0;
        if (waitSec > 0) parts.push('约 ' + waitSec + ' 秒后自动认领');
        const baseDetail = operationsText(incident.detail || incident.reason || '').replace(/\s+/g, ' ').trim();
        if (baseDetail) parts.push(baseDetail);
        return { label: view.label, hint: sentence(parts.join('；')), error: false };
      }
      if (!batchName && host.incidentCount > 0) return { label: '已触发', hint: host.incidentCount + ' 个故障待接手', error: false };
      const monitoring = host.running === true && host.loopAlive === true && Number.isFinite(scannedAt);
      return { label: monitoring ? '持续监测中' : '监测待确认', hint: '', error: false };
    }
    async function operationsRequest(path, body, signal) {
      const controller = new AbortController();
      const abort = () => controller.abort();
      if (signal && signal.aborted) abort();
      else if (signal) signal.addEventListener('abort', abort, { once: true });
      const timer = setTimeout(abort, 15000);
      try {
        const response = await window.fetch('/wf-plugin/operations/' + path, {
          method: body === undefined ? 'GET' : 'POST', signal: controller.signal,
          headers: { accept: 'application/json', ...(body === undefined ? {} : { 'content-type': 'application/json' }) },
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        });
        const text = await response.text();
        let data;
        try { data = JSON.parse(text); } catch (_) { throw new Error('运维接口返回了无法读取的内容（HTTP ' + response.status + '）'); }
        if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('运维接口返回格式不完整');
        if (!response.ok || (path !== 'status' && data.error)) throw new Error(operationsText(data.error || data.message) || '运维请求失败（HTTP ' + response.status + '）');
        return data;
      } catch (e) {
        if (e.name === 'AbortError') throw new Error('运维请求已取消或超过15秒，请刷新核实');
        throw e;
      } finally {
        clearTimeout(timer);
        if (signal) signal.removeEventListener('abort', abort);
      }
    }
    function operationsStatusTone(summary) {
      if (!summary) return 'idle';
      if (summary.error) return 'error';
      const label = String(summary.label || '');
      if (['持续监测中', '已恢复', '流水线已复验', '已移出排队区'].includes(label)) return 'ok';
      if (['调查中', '修复中', '复验中', '准备修复', '定位代码', '修改中', '测试中', '准备安装', '安装中', '自动升级修复', '已触发'].includes(label)) return 'active';
      if (['等待新证据', '等待原流程续跑', '等待修复锁', '等待重启复验', '等待批次执行锁释放', '待外部条件', '冷却等待', '排队', '待核对动作回执', '复验待确认', '释放待接续', '修复无进展'].includes(label)) return 'waiting';
      return 'idle';
    }
    function operationsFeedItems(host, record, options = {}) {
      if (options.recordError || (host && host.enabled === false)) return [];
      const batchName = options.batchName || '';
      const current = record && (!batchName || record.batchName === batchName) ? record : null;
      const incident = current && current.incident && typeof current.incident === 'object' ? current.incident : null;
      const attempts = Array.isArray(host && host.attempts) ? host.attempts : [];
      const activeAttempt = attempts.slice().reverse().find((a) => a && a.active && (!batchName || a.batch_name === batchName));
      const fmtTime = (value) => {
        if (!value) return '';
        const d = typeof value === 'number' ? new Date(value > 1e12 ? value : value * 1000) : new Date(value);
        if (isNaN(d.getTime())) return '';
        const p = (n) => String(n).padStart(2, '0');
        return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
      };
      const actionLabels = {
        claim: '接手处理',
        diagnose: '诊断分析',
        read_evidence: '读取证据',
        recover: '恢复尝试',
        preview_repair: '预演修复',
        apply_repair: '应用修复',
        regenerate_units: '局部重生成',
        request_code_repair: '准备修复区',
        code_repair: '代码修复',
        install_code_repair: '安装修复',
        verify: '复验检查',
        resume: '触发续跑',
        finish: '完成本轮',
        unfreeze: '解除冻结',
        reconcile: '核对回执',
        recovery: '恢复调度',
      };
      const phaseLabels = {
        model_wait: '等待模型响应',
        evidence_read: '正在定位相关代码',
        code_write: '正在修改代码',
        code_test: '正在运行回归测试',
        code_prepare: '正在准备安装补丁',
        code_install: '正在安装代码修复',
        verifying: '正在复验检查',
        no_substantive_progress: '本轮未产生实质进展',
      };
      const items = [];
      const seen = new Set();
      const push = (time, tag, text) => {
        const clean = operationsText(text).replace(/\s+/g, ' ').trim();
        if (!clean) return;
        const key = tag + '|' + clean;
        if (seen.has(key)) return;
        seen.add(key);
        items.push({ time: fmtTime(time), tag, text: clean });
      };
      if (activeAttempt) {
        const phaseText = phaseLabels[activeAttempt.phase] || '';
        const detailText = operationsText(activeAttempt.detail || activeAttempt.summary || '');
        const liveText = [phaseText, detailText].filter(Boolean).join('：');
        if (liveText) push(activeAttempt.updated_at || activeAttempt.started_at, '当前动作', liveText);
      }
      if (incident && incident.repair_progress && typeof incident.repair_progress === 'object') {
        const rp = incident.repair_progress;
        const rpText = [phaseLabels[rp.phase] || operationsText(rp.phase), operationsText(rp.operation)].filter(Boolean).join(' · ');
        if (rpText) push(rp.at, '修复进度', rpText);
      }
      if (incident && incident.recovery && typeof incident.recovery === 'object') {
        const rec = incident.recovery;
        const recText = [operationsText(rec.reason), operationsText(rec.next_action)].filter(Boolean).join(' · ');
        if (recText) push(rec.at, '恢复调度', recText);
      }
      const events = incident && Array.isArray(incident.events) ? incident.events.filter((e) => e && typeof e === 'object') : [];
      const substantive = events.filter((e) => !['heartbeat', 'progress'].includes(e.action)).slice(-20).reverse();
      for (const ev of substantive) {
        const actionKey = operationsText(ev.action || ev.type || ev.event || ev.kind);
        const tag = actionLabels[actionKey] || actionKey || '处理动态';
        const outcome = operationsText(ev.outcome || ev.result || ev.message);
        const detail = operationsText(ev.detail || ev.summary);
        const body = [detail, outcome && outcome !== 'completed' ? outcome : ''].filter(Boolean).join(' · ') || outcome;
        push(ev.timestamp || ev.created_at || ev.at || ev.time, tag, body);
      }
      const findings = incident && Array.isArray(incident.findings) ? incident.findings.filter((f) => f && typeof f === 'object').slice(-5).reverse() : [];
      for (const f of findings) {
        push(f.at || f.timestamp, '诊断发现', f.summary || f.detail || '');
      }
      return items;
    }
    function OperationsPanel({ batchName }) {
      const React = require('react'), h = React.createElement;
      const [host, setHost] = React.useState({ enabled: true, available: false, loaded: false });
      const [record, setRecord] = React.useState(null);
      const [hostError, setHostError] = React.useState('');
      const [recordError, setRecordError] = React.useState('');
      const [busy, setBusy] = React.useState(false);
      const [refresh, setRefresh] = React.useState(0);
      const [showDetail, setShowDetail] = React.useState(false);
      const [detailData, setDetailData] = React.useState(null);
      const generation = React.useRef(0);
      const saving = React.useRef(false);
      const configVersion = React.useRef(0);
      React.useEffect(() => {
        const seq = ++generation.current;
        const controller = new AbortController();
        let timer;
        setRecord(null); setRecordError('');
        async function readStatus() {
          // A config write owns host state until its acknowledgement arrives.
          if (!saving.current) {
            const version = configVersion.current;
            try {
              const data = await operationsRequest('status', undefined, controller.signal);
              if (seq === generation.current && !saving.current && version === configVersion.current) {
                if (typeof data.enabled !== 'boolean' || typeof data.available !== 'boolean') throw new Error('运维状态字段不完整');
                setHost({ ...data, loaded: true }); setHostError(operationsText(data.error));
              }
            } catch (e) {
              if (seq === generation.current && version === configVersion.current) { setHostError(operationsText(e.message)); setHost((old) => ({ ...old, loaded: false, available: false })); }
            }
          }
          if (batchName) {
            try {
              const data = await jget('/batches/' + encodeURIComponent(batchName) + '/operations', controller.signal);
              if (!data || !Object.prototype.hasOwnProperty.call(data, 'incident') || (data.incident !== null && (typeof data.incident !== 'object' || Array.isArray(data.incident)))) throw new Error('运维记录格式不完整');
              if (seq === generation.current) { setRecord({ batchName, incident: data.incident, receipts: data.receipts || [] }); setRecordError(''); }
            } catch (e) {
              if (seq === generation.current) setRecordError(operationsText(e.message));
            }
          }
          if (seq === generation.current) timer = setTimeout(readStatus, 3000);
        }
        readStatus();
        return () => { generation.current++; controller.abort(); clearTimeout(timer); };
      }, [batchName, refresh]);
      async function toggle() {
        if (saving.current || !host.loaded) return;
        saving.current = true; configVersion.current++; setBusy(true); setHostError('');
        const seq = generation.current;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
          const data = await operationsRequest('config', { enabled: !host.enabled }, controller.signal);
          if (typeof data.enabled !== 'boolean') throw new Error('接口未确认启停结果，请刷新核实');
          if (seq === generation.current) setHost((old) => ({ ...old, enabled: data.enabled }));
        } catch (e) {
          if (seq === generation.current) { setHostError('启停结果未确认：' + operationsText(e.message) + '。请刷新核实。'); setHost((old) => ({ ...old, loaded: false })); }
        } finally {
          clearTimeout(timeout); saving.current = false;
          if (seq === generation.current) setBusy(false);
        }
      }
      const summary = operationsSummary(host, record, { batchName, hostError, recordError });
      const tone = operationsStatusTone(summary);
      const incident = record && record.incident && typeof record.incident === 'object' ? record.incident : null;
      const feedItems = operationsFeedItems(host, record, { batchName, recordError });
      const isBatchClaimed = Boolean(
        (incident && ['claimed', 'investigating', 'repairing', 'verifying'].includes(incident.status))
        || ['调查中', '修复中', '维护中', '诊断中', '验证中'].includes(summary.label)
        || (Array.isArray(host.attempts) && host.attempts.some((a) => a && a.active && a.batch_name === batchName))
      );
      const unknownCount = Array.isArray(record && record.receipts)
        ? record.receipts.filter((r) => r && r.status === 'unknown').length : 0;
      const canReconcileUnknown = Boolean(batchName && unknownCount > 0);
      const canTakeover = Boolean(batchName && host.enabled && host.loaded && !isBatchClaimed && !canReconcileUnknown);
      const canUnfreeze = Boolean(batchName && !canReconcileUnknown && incident && ['waiting_evidence', 'waiting_external'].includes(incident.status));
      const hasDetail = Boolean(incident && ((Array.isArray(incident.findings) && incident.findings.length > 0) || incident.recovery || incident.code_repair || canUnfreeze || canReconcileUnknown));
      async function reconcileUnknown() {
        if (!batchName || busy) return;
        setBusy(true); setRecordError('');
        try {
          await jpost('/batches/' + encodeURIComponent(batchName) + '/operations/reconcile-unknown', { note: 'ui_manual_reconcile' });
          setRefresh((n) => n + 1);
        } catch (e) {
          setRecordError('解除隔离失败：' + operationsText(e.message));
        } finally {
          setBusy(false);
        }
      }
      async function takeover() {
        if (!batchName || busy) return;
        setBusy(true); setRecordError('');
        try {
          const res = await jpost('/batches/' + encodeURIComponent(batchName) + '/operations/takeover', {});
          if (res && res.triggered === false && res.reason) {
            setRecordError(operationsText(res.reason));
          }
          setRefresh((n) => n + 1);
        } catch (e) {
          setRecordError('立即接手失败：' + operationsText(e.message));
        } finally {
          setBusy(false);
        }
      }
      async function unfreeze() {
        if (!batchName || busy) return;
        setBusy(true); setRecordError('');
        try {
          await jpost('/batches/' + encodeURIComponent(batchName) + '/operations/unfreeze', {});
          setRefresh((n) => n + 1);
        } catch (e) {
          setRecordError('解除冻结失败：' + operationsText(e.message));
        } finally {
          setBusy(false);
        }
      }
      async function openDetail() {
        const next = !showDetail;
        if (typeof setShowDetail === 'function') setShowDetail(next);
        if (next && batchName) {
          try {
            const [perf, diff] = await Promise.all([
              jget('/batches/' + encodeURIComponent(batchName) + '/operations/performance').catch(() => null),
              jget('/batches/' + encodeURIComponent(batchName) + '/operations/diff').catch(() => null),
            ]);
            if (typeof setDetailData === 'function') setDetailData({ perf, diff });
          } catch (_) {}
        }
      }
      const emptyFeedText = !host.enabled
        ? '开启后将自动监测并实时显示处理动态。'
        : (summary.label === '持续监测中' ? '当前运行平稳，处理动态将实时显示在这里。' : '实时处理动态将显示在这里。');
      return h('section', {
        'aria-label': 'AI运维',
        className: 'wf-operations-card',
        style: {
          marginTop: 0,
          marginBottom: 0,
          paddingTop: 8,
          paddingRight: 14,
          paddingBottom: 10,
          paddingLeft: 14,
          height: 176,
          minWidth: 0,
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          border: '1px solid var(--dsw-alias-border-l1)',
          borderRadius: 12,
          background: 'var(--dsw-alias-bg-layer-1)',
          boxShadow: '0 1px 2px rgba(16,24,40,.04), 0 8px 24px rgba(16,24,40,.035)',
        },
      },
        h('div', { className: 'wf-operations-toolbar', style: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'nowrap', minWidth: 0, flexShrink: 0 } },
          h('strong', { style: { ...T.sectionTitle, flexShrink: 0, whiteSpace: 'nowrap' } }, 'AI运维'),
          h('span', { className: 'wf-operations-status', 'data-tone': tone, role: 'status', 'aria-live': 'polite', style: { marginRight: 'auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' } }, summary.label),
          canReconcileUnknown ? h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: reconcileUnknown }, '解除隔离') : null,
          canTakeover ? h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: takeover }, '立即接手') : null,
          canUnfreeze ? h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: unfreeze }, '解除冻结') : null,
          hasDetail ? h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: openDetail }, showDetail ? '收起详情' : '详情') : null,
          h('button', { type: 'button', className: 'wf-btn', role: 'switch', 'aria-label': 'AI运维', 'aria-checked': host.enabled, disabled: busy || !host.loaded, style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, onClick: toggle }, busy ? '保存中…' : host.enabled ? '关闭运维' : '启动运维'),
          h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: () => setRefresh((n) => n + 1) }, '刷新')),
        h('div', {
          className: 'wf-operations-feed wf-scroll',
          style: {
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            overflowX: 'hidden',
            marginTop: 6,
            paddingTop: 6,
            borderTop: '1px solid var(--dsw-alias-border-l1)',
            display: 'flex',
            flexDirection: 'column',
            gap: 5,
          },
        },
          summary.hint ? h('p', {
            className: 'wf-operations-hint',
            role: summary.error ? 'alert' : 'status',
            title: summary.hint,
            style: { ...(summary.error ? T.statusErr : T.hint), margin: 0, flexShrink: 0, overflowWrap: 'anywhere', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' },
          }, summary.hint) : null,
          feedItems.length > 0
            ? feedItems.map((item, idx) => h('div', { key: 'feed:' + idx, className: 'wf-operations-feed-item' },
                item.time ? h('span', { className: 'wf-operations-feed-time' }, item.time) : null,
                h('span', { className: 'wf-operations-feed-tag' }, item.tag),
                h('span', { className: 'wf-operations-feed-text' }, item.text)))
            : (!summary.hint ? h('div', { style: { ...T.hint, fontSize: 11.5, margin: 'auto 0', opacity: .82 } }, emptyFeedText) : null)),
        showDetail && hasDetail ? h('div', {
          className: 'wf-operations-detail-modal',
          role: 'dialog',
          'aria-label': 'AI运维详情',
          style: {
            position: 'fixed', right: 24, bottom: 24, zIndex: 2600,
            width: 'min(480px, calc(100vw - 32px))',
            maxHeight: 'min(420px, calc(100vh - 96px))',
            overflowY: 'auto',
            padding: 12,
            border: '1px solid var(--dsw-alias-border-l2)',
            borderRadius: 10,
            background: 'var(--dsw-alias-bg-layer-1)',
            boxShadow: '0 12px 36px rgba(0,0,0,.22)',
            fontSize: 12,
            lineHeight: 1.55,
          },
        },
          h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6, paddingBottom: 6, borderBottom: '1px solid var(--dsw-alias-border-l1)' } },
            h('strong', null, `事件详情（${incident.stage || '-'} / ${incident.status || '-'}）`),
            h('button', { type: 'button', className: 'wf-btn', style: T.miniBtn, onClick: () => typeof setShowDetail === 'function' && setShowDetail(false) }, '关闭')),
          incident.recovery && incident.recovery.reason ? h('div', null, '当前阻塞/恢复说明：' + operationsText(incident.recovery.reason)) : null,
          Array.isArray(incident.findings) && incident.findings.length > 0 ? h('div', { style: { marginTop: 4 } }, '最近诊断发现：' + operationsText(incident.findings[incident.findings.length - 1].summary || '')) : null,
          detailData && detailData.perf && detailData.perf.budgets ? h('div', { style: { marginTop: 4 } }, `预算使用：尝试 ${detailData.perf.budgets.attempts}/${detailData.perf.budgets.attempts_limit} · 安装 ${detailData.perf.budgets.installs}/${detailData.perf.budgets.installs_limit} · 重生成 ${detailData.perf.budgets.regenerations}/${detailData.perf.budgets.regenerations_limit}`) : null,
          detailData && detailData.diff && detailData.diff.patch ? h('pre', { style: { marginTop: 6, maxHeight: 180, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', padding: 6, background: 'var(--dsw-alias-bg-base)', borderRadius: 6, border: '1px solid var(--dsw-alias-border-l1)' } }, detailData.diff.patch) : null) : null);
    }

    async function requestAdvance(name, post, get, timeoutMs = 15000) {
      const path = '/batches/' + encodeURIComponent(name);
      let timer;
      try {
        const receipt = await Promise.race([
          post(path + '/advance', {}),
          new Promise((resolve, reject) => { timer = setTimeout(() => reject(new Error('请求回执超时')), timeoutMs); }),
        ]);
        if (!receipt || typeof receipt !== 'object') throw new Error('后台未返回有效回执');
        return receipt;
      } catch (error) {
        // 请求可能已送达；先只读核对，绝不自动重发推进请求。
        if (error.httpStatus) throw error;
        try {
          const state = await get(path + '/pipeline');
          if (state && (state.running || state.queued || state.waiting || state.external)) return state;
        } catch (_) { /* 下方明确显示未知结果，不把网络失败当执行失败 */ }
        throw new Error('继续处理请求结果未确认：' + String(error.message || error) + '；请查看进度或刷新，系统未自动重复提交');
      } finally {
        clearTimeout(timer);
      }
    }
    function advanceReceiptFeedback(receipt) {
      const state = receipt || {};
      if (state.error) return { error: true, message: String(state.error) };
      const stageLabel = state.active_stage_label || state.next_stage_label || '';
      const elapsed = typeof state.elapsed_seconds === 'number' ? ('，已运行 ' + state.elapsed_seconds + 's') : '';
      const detail = state.active_detail ? ('（' + state.active_detail + elapsed + '）') : (elapsed ? ('（' + elapsed.slice(1) + '）') : '');
      const blockedNote = state.blocked_reason ? ('；当前阻断：' + state.blocked_reason) : '';
      if (state.waiting) {
        return { message: state.reason || ('等待前一份试卷' + (state.waiting_for ? '「' + state.waiting_for + '」' : '') + '完成后自动从' + (stageLabel ? '「' + stageLabel + '」' : '') + '继续') };
      }
      if (state.external || (state.running && !state.started)) {
        if (stageLabel) return { message: '当前已在执行「' + stageLabel + '」' + detail + '，无需重复点击' };
        return { message: state.external ? '该批次正由另一执行器处理，正在同步进度' : '该批次已在处理中，正在同步进度' };
      }
      if (state.started && state.queued) {
        const cleared = state.cleared_cooldown ? '（已清除退避倒计时）' : '';
        const pos = Number(state.queue_position) || 1;
        if (pos <= 1) {
          return { message: '已立即启动' + (stageLabel ? '「' + stageLabel + '」' : '下一阶段') + cleared + blockedNote };
        }
        return { message: '已加入队列（第 ' + pos + ' 位），轮到后从' + (stageLabel ? '「' + stageLabel + '」' : '当前阶段') + '继续' + cleared + blockedNote };
      }
      if (state.queued) {
        const pos = Number(state.queue_position);
        return { message: '已在处理队列中' + (pos > 0 ? '（第 ' + pos + ' 位）' : '') + (stageLabel ? '，待执行「' + stageLabel + '」' : '') };
      }
      if (state.started) return { message: '已启动' + (stageLabel ? '「' + stageLabel + '」' : '继续处理') + '，正在刷新进度' };
      return { error: true, message: state.reason || '后台未启动处理，且未返回排队或等待状态，请刷新查看诊断' };
    }
    function advanceProgressFeedback(state, receipt) {
      if (receipt && receipt.error) return receipt;
      if (state && (state.running || state.queued || state.waiting || state.external)) return advanceReceiptFeedback(state);
      if (state && state.result) {
        const result = state.result;
        if (result.error) return { error: true, message: '后台处理失败：' + String(result.error) };
        if (result.reason) return { error: ['failed', 'blocked'].includes(result.status), message: '本轮停在「' + (result.stopped_at || '当前阶段') + '」：' + result.reason };
        return { message: '本轮处理已结束，具体结果见流程及卡点诊断' };
      }
      return receipt || { message: '' };
    }
    function recoveryProgressText(advance) {
      if (!advance) return '';
      // 严格串行：前一份未完成且未移出排队区前，后面的批次不开始。
      if (advance.waiting) {
        return '排队等待：需等前一份「' + (advance.waiting_for || '试卷') + '」完成后自动开始';
      }
      // 全局串行队列：前一份没跑完时，其它批次显示“排队等待”，不并跑。
      if (advance.queued && !advance.running) return '排队等待：前一份试卷跑完后自动开始';
      if (advance.running && advance.active_stage_label) {
        const elapsed = typeof advance.elapsed_seconds === 'number' ? ('（已运行 ' + advance.elapsed_seconds + 's）') : '';
        const detail = advance.active_detail ? (' · ' + advance.active_detail) : '';
        return '正在执行「' + advance.active_stage_label + '」' + detail + elapsed;
      }
      const healing = advance.self_heal || {};
      const phase = healing.status || '';
      if (phase === 'diagnosing') return advance.running ? 'AI 自动修复：正在判断卡点原因' : 'AI 自动修复：已接管，等待恢复检测';
      // 诊断调用被后端/插件重启打断（不是题目内容失败）：状态已自动收尾并退还预算。
      if (phase === 'diagnostic_interrupted') return 'AI 自动修复：上次诊断被重启中断，不是题目问题，已自动重新判断';
      if (phase === 'repaired') return 'AI 自动修复：已执行局部修复，继续校验';
      if (phase === 'requeued') return healing.has_feedback ? 'AI 自动修复：已安排受影响题目带反馈重试' : 'AI 自动修复：已安排当前阶段重试';
      if (phase === 'verified') return 'AI 自动修复：重试后校验已通过';
      if (['failed', 'retry_failed', 'recorded'].includes(phase)) return 'AI 自动修复：本轮尚未修复，已安排再次判断';
      if (phase === 'waiting_external') return 'AI 自动修复：等待所需输入或服务恢复，将自动重试';
      const status = (advance.supervisor || {}).status;
      if (['cooldown', 'waiting_external', 'exhausted'].includes(status)) return '自动恢复：已安排稍后重试，无需反复点击继续';
      return '';
    }
    // 仅表达分类间的真实依赖；类内交接与处理步骤只在悬浮层展开。
    function flowDependencies() {
      return [
        ['extract', 'structure_gate'], ['structure_gate', 'preassemble'],
        ['preassemble', 'audio'], ['preassemble', 'answers'],
        ['preassemble', 'explanations'], ['preassemble', 'knowledge'],
        ['answers', 'assemble'], ['explanations', 'assemble'], ['knowledge', 'assemble'],
        ['assemble', 'validate'], ['validate', 'review'], ['audio', 'review'],
        ['review', 'format_gate'], ['format_gate', 'upload'],
      ];
    }
    // 主图固定 12 类；状态归并真实 stage，不改变后台执行阶段。
    function compactFlowStages(sourceItems, overview = false) {
      const groups = [
        { id: 'extract', title: '内容提取', ids: ['extract'] },
        { id: 'structure_gate', title: '结构确认', ids: ['structure_gate'] },
        { id: 'preassemble', title: '组装判型', ids: ['preassemble', 'types'] },
        { id: 'audio', title: '听力处理', ids: ['audio'] },
        { id: 'answers', title: '答案填充', ids: ['answers'] },
        { id: 'explanations', title: '解析填充', ids: ['explanations'] },
        { id: 'knowledge', title: '知识标注', ids: ['knowledge'] },
        { id: 'assemble', title: '汇总整理', ids: ['assemble', 'meta', 'underlines'] },
        { id: 'validate', title: '本地体检', ids: ['validate'] },
        { id: 'review', title: '内容审核', ids: ['review', 'review_gate'] },
        { id: 'format_gate', title: '格式检查', ids: ['format_gate'] },
        { id: 'upload', title: '审核上传', ids: ['upload_gate', 'upload', 'publish_gate'] },
      ].map((group) => ({ ...group, members: [] }));
      // 无任务时复用同一分类与依赖图，仅展示结构，不构造任何批次或执行状态。
      if (overview) return groups.map((group) => ({
        id: group.id, title: group.title, stage_ids: group.ids,
        inProgress: false, members: [], substeps: [],
      }));
      const lookup = new Map();
      groups.forEach((group) => group.ids.forEach((id) => lookup.set(id, group)));
      let preceding = groups[0];
      (sourceItems || []).forEach((item) => {
        // 未知的兼容阶段保留在前一分类的悬浮层，不增加主图分类。
        const group = lookup.get(item.id) || preceding;
        group.members.push(item);
        preceding = group;
      });
      const done = { completed: true, approved: true, skipped: true };
      return groups.filter((group) => group.members.length).map((group) => {
        const members = group.members;
        const executionAccepted = (item) => item.status === 'needs_review'
          && item.execution_completed === true && item.continue_unqualified === true && !item.release_ready;
        const states = members.map((item) => executionAccepted(item) ? 'completed' : item.status || 'pending');
        const hasFindings = members.some(executionAccepted);
        let status = 'pending';
        if (states.every((state) => done[state])) status = hasFindings ? 'completed_with_findings' : states.every((state) => state === 'skipped') ? 'skipped' : 'completed';
        else status = ['failed', 'blocked', 'running', 'invalidated', 'needs_review'].find((state) => states.includes(state)) || 'pending';
        // 显式 active 覆盖兼容 inProgress（包括 active=false）；仅缺 active 时回退。
        const inProgress = members.some((item) => typeof item.active === 'boolean'
          ? item.active : typeof item.inProgress === 'boolean' ? item.inProgress : item.status === 'running');
        const description = members.map((item) => shortStageName(item) + '：' + (executionAccepted(item) ? '已执行，有质量差异（未放行）' : statusText(item.status))
          + (item.description ? '\n' + item.description : '') + (item.notes ? '\n' + item.notes : '')).join('\n');
        const displayMember = (item) => ({ id: item.id, title: shortStageName(item),
          status: executionAccepted(item) ? 'completed_with_findings' : (item.status || 'pending') });
        return { id: group.id, title: group.title, status, inProgress,
          stage_ids: members.map((item) => item.id),
          description, done: states.filter((state) => done[state]).length, total: members.length,
          members: members.map(displayMember),
          // 只展开后台证据，不由父阶段状态推断内部步骤完成。旧响应缺步骤时回退 stage。
          substeps: members.flatMap((item) => {
            const steps = Array.isArray(item.substeps) ? item.substeps.filter((step) => step && typeof step === 'object') : [];
            return steps.length ? steps.map((step) => ({ id: step.id, title: shortStageName(step), status: step.status || 'pending' })) : [displayMember(item)];
          }) };
      });
    }
    function statusText(s) {
      return ({ completed: '已完成', completed_with_findings: '已执行，有差异', approved: '已批准', skipped: '已跳过', failed: '失败', blocked: '受阻', needs_review: '待核验', invalidated: '需重跑', pending: '待处理', running: '处理中' })[s] || (s || '待处理');
    }
    // 标题以服务端阶段契约为准；仅在旧响应缺标题时兜底，不在客户端维护阶段顺序。
    // 排队区的“已完成/总数”必须用流程图的实际 12 类，不能用内部 18 个
    // 阶段的台账数字，否则列表会出现用户在图上看不到的“十几/18”。
    function compactProgress(stage) {
      const items = (stage && stage.items) || [];
      if (!items.length) return { done: (stage && stage.done) || 0, total: (stage && stage.total) || 0 };
      const finished = { completed: true, completed_with_findings: true, skipped: true, approved: true };
      const groups = compactFlowStages(items);
      return { done: groups.filter((group) => finished[group.status]).length, total: groups.length };
    }

    // 卡片时间：精确到「月-日 时:分」，按浏览器本地时区展示。
    function fmtMonthMinute(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      if (isNaN(d.getTime())) return '';
      const p = (n) => String(n).padStart(2, '0');
      return (d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    }
    function batchLatestTime(b) {
      const values = [b && b.flow_finished_at, b && b.flow_started_at,
                      b && b.updated_at, b && b.created_at];
      let latest = 0;
      for (const value of values) {
        const at = Date.parse(String(value || ''));
        if (Number.isFinite(at) && at > latest) latest = at;
      }
      return latest;
    }
    function flowTimingText(b) {
      const finished = fmtMonthMinute(b && b.flow_finished_at);
      if (!finished) return '';
      const mins = b && b.flow_minutes;
      const minutesText = (typeof mins === 'number' && mins >= 0) ? ('流程耗时 ' + mins + ' 分钟') : '';
      if (b.flow_done) return '完成时间：' + finished + (minutesText ? ' · ' + minutesText : '');
      return '进行中 · 最近进度 ' + finished;
    }
    function shortStageName(it) {
      const title = String((it && it.title) || '').trim();
      if (title) return title;
      const id = String((it && it.id) || '');
      const MAP = {
        extract: '原卷提取', structure_gate: '结构确认', preassemble: '前置批次组装',
        answers: '答案填充', audio: '音频填充', types: '题型填充',
        explanations: '解析与大意填充', knowledge: '知识点填充', assemble: '填充汇总',
        meta: '元数据补全', underlines: '画线补全', validate: '源文与内容体检',
        review: '内容与知识审核', review_gate: '内容审核放行', format_gate: '上传格式检查',
        upload_gate: '上传人工确认', upload: '上传', publish_gate: '发布人工确认',
      };
      return MAP[id] || id;
    }

    function MaterialRoleFixer(props) {
      const React = require('react');
      const h = React.createElement;
      const { batchName, onRerunStarted } = props;
      const [docs, setDocs] = React.useState([]);
      const [draft, setDraft] = React.useState({});
      const [loading, setLoading] = React.useState(true);
      const [saving, setSaving] = React.useState(false);
      const [error, setError] = React.useState('');
      const [doneMsg, setDoneMsg] = React.useState('');

      React.useEffect(() => {
        let cancelled = false;
        setLoading(true); setError(''); setDoneMsg('');
        jget('/batches/' + encodeURIComponent(batchName) + '/material-roles')
          .then((d) => {
            if (cancelled) return;
            const list = (d && d.documents) || [];
            setDocs(list);
            const next = {};
            for (const row of list) {
              const eff = row.user_role || row.detected_role || 'other';
              next[row.file] = eff === 'explanations' ? 'answers' : eff;
            }
            setDraft(next);
          })
          .catch((e) => { if (!cancelled) setError('加载材料列表失败：' + String((e && e.message) || e)); })
          .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
      }, [batchName]);

      async function applyAndRerun() {
        if (saving || !docs.length) return;
        setSaving(true); setError(''); setDoneMsg('');
        try {
          await jpost('/batches/' + encodeURIComponent(batchName) + '/material-roles', { roles: draft, rerun: true });
          setDoneMsg('已保存材料角色并启动重跑提取…');
          if (onRerunStarted) onRerunStarted();
        } catch (e) {
          setError('保存失败：' + String((e && e.message) || e));
        } finally {
          setSaving(false);
        }
      }

      const selectable = MATERIAL_ROLE_OPTIONS.filter((o) => o.value !== 'auto');
      return h('div', { style: { marginTop: 6, padding: '8px 10px', borderRadius: 8, background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l2)', display: 'flex', flexDirection: 'column', gap: 6 } },
        h('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 } },
          h('span', { style: { fontSize: 12, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' } }, '手动指定各材料角色并重跑提取'),
          h('button', {
            type: 'button', className: 'wf-btn-primary',
            style: { ...T.miniBtn, background: 'var(--wf-brand)', color: '#fff', border: 'none', padding: '3px 10px' },
            disabled: loading || saving || !docs.length,
            onClick: applyAndRerun,
          }, saving ? '正在重跑…' : '确认角色并重跑提取'),
        ),
        loading ? h('div', { style: { ...T.hint, fontSize: 11.5 } }, '正在读取材料列表…') : null,
        !loading && docs.length ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: 5 } },
          docs.map((row) => h('div', { key: row.file, style: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 11.5 } },
            h('span', { title: (row.original_filename || row.file) + ' (' + row.file + ')', style: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--dsw-alias-label-primary)' } }, row.original_filename || row.file),
            row.detected_role ? h('span', { style: { ...T.hint, fontSize: 10.5, flexShrink: 0 } }, '原判：' + (MATERIAL_ROLE_LABELS[row.detected_role] || row.detected_role)) : null,
            h('select', {
              'aria-label': (row.original_filename || row.file) + ' 角色',
              value: draft[row.file] || 'other',
              disabled: saving,
              onChange: (e) => setDraft((prev) => ({ ...prev, [row.file]: e.target.value })),
              style: { fontSize: 11.5, padding: '2px 6px', borderRadius: 5, border: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-alias-bg-layer-1)', color: 'var(--dsw-alias-label-primary)', flexShrink: 0 },
            }, selectable.map((opt) => h('option', { key: opt.value, value: opt.value }, opt.label))),
          )),
        ) : null,
        error ? h('div', { style: { ...T.statusErr, fontSize: 11.5, margin: 0 } }, error) : null,
        doneMsg ? h('div', { style: { ...T.statusOk, fontSize: 11.5, margin: 0 } }, doneMsg) : null,
      );
    }

    function copyDiagText(text) {
      const str = String(text || '');
      const fallback = () => {
        try {
          if (typeof document === 'undefined') return false;
          const ta = document.createElement('textarea');
          ta.value = str;
          ta.setAttribute('readonly', '');
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          ta.style.left = '-9999px';
          document.body.appendChild(ta);
          ta.select();
          const ok = document.execCommand('copy');
          document.body.removeChild(ta);
          return Boolean(ok);
        } catch (_) {
          return false;
        }
      };
      if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        return navigator.clipboard.writeText(str).then(() => true).catch(() => fallback());
      }
      return Promise.resolve(fallback());
    }

    function buildDiagLogText(selBatch, stage, shown, flowFeedback) {
      const lines = ['【卡点诊断日志】'];
      const batchName = String((selBatch && selBatch.name) || '').trim();
      const paperTitle = String((selBatch && selBatch.paper && selBatch.paper.title) || '').trim();
      if (batchName) lines.push('批次：' + batchName);
      if (paperTitle && paperTitle !== batchName) lines.push('试卷：' + paperTitle);
      if (stage && stage.current) {
        lines.push('当前阶段：' + (shortStageName(stage.current) || stage.current.id || '') + ' (' + (stage.current.id || '') + ' / ' + (stage.current.status || '') + ')');
      }
      if (stage && stage.hint) lines.push('流程提示：' + stage.hint);
      if (flowFeedback && flowFeedback.error && flowFeedback.message) {
        lines.push('推进提示：' + String(flowFeedback.message));
      }
      if (selBatch && selBatch.advance && selBatch.advance.error) {
        lines.push('推进报错：' + String(selBatch.advance.error));
      }
      lines.push('卡点数量：' + shown.length);
      shown.forEach((it, idx) => {
        lines.push('');
        lines.push('--- 卡点 ' + (idx + 1) + ' ---');
        lines.push('阶段：' + (shortStageName(it) || it.id || '') + (it.id ? ' (' + it.id + ')' : ''));
        lines.push('状态：' + statusText(it.status) + (it.status ? ' (' + it.status + ')' : ''));
        if (it.reason) lines.push('摘要：' + String(it.reason));
        if (it.detail) lines.push('详细原因：\n' + String(it.detail));
        if (it.notes && String(it.notes) !== String(it.detail || '')) lines.push('备注：\n' + String(it.notes));
        if (it.hint) lines.push('处理建议：' + String(it.hint));
        if (it.command) lines.push('修复命令：' + String(it.command));
        const badSubs = Array.isArray(it.substeps)
          ? it.substeps.filter((s) => s && (s.status === 'failed' || s.status === 'blocked' || s.status === 'needs_review'))
          : [];
        if (badSubs.length) {
          lines.push('异常子步骤：');
          badSubs.forEach((s) => {
            lines.push('  - ' + (s.title || s.id || '') + '：' + statusText(s.status) + (s.detail ? ' — ' + String(s.detail) : ''));
          });
        }
      });
      return lines.join('\n');
    }

    // 卡点诊断悬浮卡片：平时收起为流程图右下角的红色圆角矩形，点击放大查看详情并支持一键复制诊断日志，再点击缩小。
    function DiagFloatingWidget(props) {
      const React = require('react');
      const h = React.createElement;
      const { selBatch, stage, shown, flowFeedback, onRefresh } = props;
      const [expanded, setExpanded] = React.useState(false);
      const [copiedLog, setCopiedLog] = React.useState(false);
      const [copiedCmdIdx, setCopiedCmdIdx] = React.useState(-1);
      const timerRef = React.useRef(null);
      React.useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);

      const batchLabel = String((selBatch && selBatch.name) || (selBatch && selBatch.paper && selBatch.paper.title) || '').trim();
      const needsRoleFix = shown.some((it) => /MATERIAL_MULTIPLE_PAPERS|MATERIAL_PAPER_UNIDENTIFIED|多份不同正文|未识别到试卷正文/.test(String(it.detail || it.notes || it.reason || '')));
      const hasAdvanceErr = Boolean(flowFeedback && flowFeedback.error && flowFeedback.message);
      const issueCount = shown.length || (hasAdvanceErr ? 1 : 0);

      const handleCopyLog = (e) => {
        if (e && e.stopPropagation) e.stopPropagation();
        const text = buildDiagLogText(selBatch, stage, shown, flowFeedback);
        copyDiagText(text).then((ok) => {
          if (!ok) return;
          setCopiedLog(true);
          if (timerRef.current) clearTimeout(timerRef.current);
          timerRef.current = setTimeout(() => setCopiedLog(false), 1800);
        });
      };

      const handleCopyCmd = (cmd, idx, e) => {
        if (e && e.stopPropagation) e.stopPropagation();
        copyDiagText(cmd).then((ok) => {
          if (!ok) return;
          setCopiedCmdIdx(idx);
          setTimeout(() => setCopiedCmdIdx((cur) => (cur === idx ? -1 : cur)), 1600);
        });
      };

      if (!expanded) {
        return h('button', {
          type: 'button',
          className: 'wf-diag-fab',
          'aria-expanded': false,
          title: '点击展开卡点诊断详情',
          onClick: () => setExpanded(true),
          style: {
            position: 'absolute', right: 12, bottom: 12, zIndex: 20,
            display: 'inline-flex', alignItems: 'center', gap: 7,
            padding: '7px 12px', borderRadius: 10, cursor: 'pointer',
            border: '1px solid var(--dsw-alias-state-error-primary)',
            background: 'var(--dsw-alias-bg-layer-1, var(--dsw-alias-bg-l1, #fff))',
            color: 'var(--dsw-alias-state-error-primary)',
            boxShadow: '0 4px 12px rgba(217,48,37,.12)',
            fontSize: 12.5, fontWeight: 600, lineHeight: 1.2,
          },
        },
          h('span', { style: { width: 7, height: 7, borderRadius: '50%', background: 'var(--dsw-alias-state-error-primary)', flexShrink: 0 } }),
          h('span', null, '卡点诊断'),
          issueCount > 0 ? h('span', {
            style: {
              fontSize: 11, fontWeight: 600, padding: '1px 6px', borderRadius: 999,
              background: 'color-mix(in srgb, var(--dsw-alias-state-error-primary) 12%, transparent)',
              color: 'var(--dsw-alias-state-error-primary)',
            },
          }, String(issueCount)) : null,
        );
      }

      return h('div', {
        className: 'wf-diag-popover',
        role: 'dialog',
        'aria-label': '卡点诊断',
        style: {
          position: 'absolute', right: 12, bottom: 12, zIndex: 30,
          width: 'min(460px, calc(100% - 24px))',
          maxHeight: 'min(420px, calc(100% - 24px))',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
          borderRadius: 12,
          border: '1px solid var(--dsw-alias-state-error-primary)',
          background: 'var(--dsw-alias-bg-layer-1, var(--dsw-alias-bg-l1, #fff))',
          boxShadow: '0 10px 28px rgba(0,0,0,.18)',
        },
      },
        h('div', {
          onClick: () => setExpanded(false),
          title: '点击缩小到右下角',
          style: {
            display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px', cursor: 'pointer', flexShrink: 0,
            borderBottom: '1px solid var(--dsw-alias-border-l2)',
            background: 'color-mix(in srgb, var(--dsw-alias-state-error-primary) 7%, var(--dsw-alias-bg-layer-1, #fff))',
          },
        },
          h('span', { style: { width: 7, height: 7, borderRadius: '50%', background: 'var(--dsw-alias-state-error-primary)', flexShrink: 0 } }),
          h('span', { style: { fontWeight: 600, fontSize: 12.5, color: 'var(--dsw-alias-state-error-primary)', flexShrink: 0 } }, '卡点诊断'),
          batchLabel ? h('span', { style: { ...T.hint, fontSize: 11.5, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: batchLabel }, batchLabel) : h('span', { style: { flex: 1 } }),
          h('button', {
            type: 'button', className: 'wf-btn-ghost',
            style: { ...T.miniBtn, flexShrink: 0, padding: '3px 9px', fontSize: 11.5, borderColor: 'var(--dsw-alias-state-error-primary)', color: 'var(--dsw-alias-state-error-primary)' },
            onClick: handleCopyLog,
          }, copiedLog ? '已复制 ✓' : '复制日志'),
          h('button', {
            type: 'button', className: 'wf-btn-ghost',
            style: { ...T.miniBtn, flexShrink: 0, padding: '3px 8px', fontSize: 11.5 },
            onClick: (e) => { if (e && e.stopPropagation) e.stopPropagation(); setExpanded(false); },
          }, '缩小'),
        ),
        h('div', { className: 'wf-scroll', style: { flex: 1, minHeight: 0, overflowY: 'auto', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 10 } },
          hasAdvanceErr ? h('div', { style: { fontSize: 12, color: 'var(--dsw-alias-state-error-primary)', wordBreak: 'break-word', padding: '6px 8px', borderRadius: 8, background: 'color-mix(in srgb, var(--dsw-alias-state-error-primary) 6%, transparent)' } },
            '推进提示：' + flowFeedback.message) : null,
          shown.map((it, i) => h('div', { key: 'd' + i, style: { display: 'flex', flexDirection: 'column', gap: 4, paddingTop: i ? 8 : 0, borderTop: i ? '1px dashed var(--dsw-alias-border-l2)' : 'none' } },
            h('div', { style: { fontSize: 12.5, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' } },
              (shortStageName(it) || it.id) + '：' + (it.reason || statusText(it.status))),
            (it.detail || it.notes)
              ? h('pre', { style: { margin: 0, padding: '6px 8px', borderRadius: 6, background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l2)', fontSize: 11.5, lineHeight: 1.5, fontFamily: 'inherit', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'var(--dsw-alias-label-secondary)' } },
                  String(it.detail || it.notes))
              : null,
            it.hint ? h('div', { style: { ...T.hint, fontSize: 11.5, wordBreak: 'break-word' } }, '怎么办：' + it.hint) : null,
            it.command
              ? h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 } },
                  h('code', { style: { flex: 1, minWidth: 0, fontSize: 11, fontFamily: 'monospace', background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 6, padding: '4px 8px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: it.command }, it.command),
                  h('button', { type: 'button', className: 'wf-btn-ghost', style: { ...T.miniBtn, flexShrink: 0 }, onClick: (e) => handleCopyCmd(it.command, i, e) }, copiedCmdIdx === i ? '已复制 ✓' : '复制命令'))
              : null,
          )),
          needsRoleFix && selBatch && selBatch.name
            ? h(MaterialRoleFixer, { key: selBatch.name, batchName: selBatch.name, onRerunStarted: onRefresh })
            : null,
        ),
      );
    }

    function diagBanner(selBatch, stage, onRefresh, flowFeedback) {
      const h = require('react').createElement;
      if (!selBatch || !stage) return null;
      const items = stage.items || [];
      const blocking = items.filter((it) => it.blocking);
      const diagnostics = stage.diagnostics || null;
      const shown = blocking.length
        ? blocking
        : (diagnostics && diagnostics.blocking_count ? [] : []);
      const hasAdvanceErr = Boolean(flowFeedback && flowFeedback.error && flowFeedback.message);
      if (!shown.length && !hasAdvanceErr) return null;
      return h(DiagFloatingWidget, {
        key: 'diag:' + (selBatch.name || ''),
        selBatch, stage, shown, flowFeedback, onRefresh,
      });
    }

    // ======================================================================
    // 知识点目录树选择组件：搜索框 + 已选知识点 + 按题型过滤的多选树状目录
    // ======================================================================
    const KNOWLEDGE_TREE_CACHE = new Map();

    function indexKnowledgeTree(items) {
      const selectableSet = new Set();
      const parentMap = new Map();
      const countMap = new Map();
      function walk(list, parentPath) {
        let total = 0;
        for (const node of list || []) {
          if (!node || !node.path) continue;
          if (parentPath) parentMap.set(node.path, parentPath);
          const isSel = Boolean(node.selectable != null ? node.selectable : node.leaf);
          if (isSel) selectableSet.add(node.path);
          const sub = walk(node.children || [], node.path);
          const own = (isSel ? 1 : 0) + sub;
          countMap.set(node.path, own);
          total += own;
        }
        return total;
      }
      walk(items, '');
      return { selectableSet, parentMap, countMap };
    }

    function filterKnowledgeTree(items, keyword) {
      const kw = String(keyword || '').trim().toLowerCase();
      if (!kw) return { items: items || [], expandPaths: new Set(), matchCount: 0 };
      const expandPaths = new Set();
      let matchCount = 0;
      function countSelectable(node) {
        const isSel = Boolean(node.selectable != null ? node.selectable : node.leaf);
        let n = isSel ? 1 : 0;
        for (const c of node.children || []) n += countSelectable(c);
        return n;
      }
      function collectBranchPaths(node) {
        if ((node.children || []).length) {
          expandPaths.add(node.path);
          for (const c of node.children) collectBranchPaths(c);
        }
      }
      function walk(list) {
        const out = [];
        for (const node of list || []) {
          if (!node || !node.path) continue;
          const nameHit = String(node.name || '').toLowerCase().includes(kw);
          const pathHit = String(node.path || '').toLowerCase().includes(kw);
          const isSel = Boolean(node.selectable != null ? node.selectable : node.leaf);
          if (nameHit || (isSel && pathHit)) {
            out.push(node);
            matchCount += countSelectable(node);
            collectBranchPaths(node);
            continue;
          }
          const kids = walk(node.children || []);
          if (kids.length) {
            expandPaths.add(node.path);
            out.push({ ...node, children: kids });
          }
        }
        return out;
      }
      return { items: walk(items), expandPaths, matchCount };
    }

    function KnowledgePicker(props) {
      const React = require('react');
      const h = React.createElement;
      const paths = Array.isArray(props.paths) ? props.paths : [];
      const mainType = String(props.mainType || '').trim();
      const onChange = props.onChange || (() => {});
      const fetchTree = props.fetchTree || ((t, signal) =>
        jget('/knowledge/tree' + (t ? '?type=' + encodeURIComponent(t) : ''), signal));
      const cacheKey = mainType || '__ALL__';
      const [query, setQuery] = React.useState('');
      const [treeData, setTreeData] = React.useState(() => KNOWLEDGE_TREE_CACHE.get(cacheKey) || null);
      const [loading, setLoading] = React.useState(() => !KNOWLEDGE_TREE_CACHE.has(cacheKey));
      const [loadError, setLoadError] = React.useState('');
      const [expanded, setExpanded] = React.useState({});
      const [locateReq, setLocateReq] = React.useState(null);
      const [locatedPath, setLocatedPath] = React.useState('');
      const treeBoxRef = React.useRef(null);

      React.useEffect(() => {
        const cached = KNOWLEDGE_TREE_CACHE.get(cacheKey);
        if (cached) {
          setTreeData(cached);
          setLoading(false);
          setLoadError('');
          return;
        }
        const ac = new AbortController();
        let alive = true;
        setLoading(true);
        setLoadError('');
        Promise.resolve(fetchTree(mainType, ac.signal))
          .then((data) => {
            if (!alive) return;
            const normalized = data && Array.isArray(data.items) ? data : { items: [], scoped: false, type: mainType, selectable_total: 0 };
            KNOWLEDGE_TREE_CACHE.set(cacheKey, normalized);
            setTreeData(normalized);
            setLoading(false);
          })
          .catch((err) => {
            if (!alive || (err && err.name === 'AbortError')) return;
            setLoadError(String((err && err.message) || err || '加载知识点目录失败'));
            setLoading(false);
          });
        return () => { alive = false; ac.abort(); };
      }, [cacheKey]);

      const treeItems = (treeData && Array.isArray(treeData.items)) ? treeData.items : [];
      const { selectableSet, parentMap, countMap } = React.useMemo(
        () => indexKnowledgeTree(treeItems),
        [treeItems],
      );

      React.useEffect(() => {
        const next = {};
        // 顶层维度不多（如完形填空只有 语篇体裁 / 主题）时默认展开一级，方便直接点选。
        if (treeItems.length > 0 && treeItems.length <= 3) {
          for (const root of treeItems) {
            if (root && root.path && (root.children || []).length) next[root.path] = true;
          }
        }
        // 自动展开当前已选知识点的所有祖先目录。
        for (const p of paths) {
          let cur = parentMap.get(p);
          while (cur) {
            next[cur] = true;
            cur = parentMap.get(cur);
          }
        }
        setExpanded((prev) => ({ ...next, ...prev }));
      }, [treeItems, paths.join('|')]);

      const searching = Boolean(query.trim());
      const filtered = React.useMemo(
        () => filterKnowledgeTree(treeItems, query),
        [treeItems, query],
      );
      const visibleItems = searching ? filtered.items : treeItems;
      const selectedSet = React.useMemo(() => new Set(paths), [paths]);
      const scoped = Boolean(treeData && treeData.scoped);

      function togglePath(p) {
        if (!p) return;
        if (selectedSet.has(p)) onChange(paths.filter((x) => x !== p));
        else onChange([...paths, p]);
      }
      function removePath(p) {
        onChange(paths.filter((x) => x !== p));
      }
      function toggleExpand(p) {
        setExpanded((prev) => ({ ...prev, [p]: !prev[p] }));
      }
      function locatePath(p) {
        if (!p || (!selectableSet.has(p) && !parentMap.has(p) && !countMap.has(p))) return;
        const next = {};
        let cur = parentMap.get(p);
        while (cur) {
          next[cur] = true;
          cur = parentMap.get(cur);
        }
        if (countMap.has(p)) next[p] = true;
        setExpanded((prev) => ({ ...prev, ...next }));
        if (query) setQuery('');
        setLocateReq({ path: p, seq: Date.now() });
      }

      React.useEffect(() => {
        if (!locateReq || !locateReq.path) return;
        const target = locateReq.path;
        const box = treeBoxRef.current;
        if (box && typeof box.querySelectorAll === 'function') {
          const rows = box.querySelectorAll('[data-path]');
          for (const el of rows) {
            if (el.getAttribute('data-path') === target) {
              if (typeof el.scrollIntoView === 'function') {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
              }
              break;
            }
          }
        }
        setLocatedPath(target);
        const timer = setTimeout(() => {
          setLocatedPath((cur) => (cur === target ? '' : cur));
        }, 1500);
        return () => clearTimeout(timer);
      }, [locateReq]);

      function renderNodes(nodes, depth) {
        const rows = [];
        for (const node of nodes || []) {
          if (!node || !node.path) continue;
          const kids = Array.isArray(node.children) ? node.children : [];
          const hasKids = kids.length > 0;
          const isSel = Boolean(node.selectable != null ? node.selectable : node.leaf);
          const isOpen = hasKids && (searching ? filtered.expandPaths.has(node.path) : Boolean(expanded[node.path]));
          const isChecked = isSel && selectedSet.has(node.path);
          const count = countMap.get(node.path) || 0;
          rows.push(h('button', {
            key: node.path,
            type: 'button',
            className: 'wf-tree-row' + (isChecked ? ' is-selected' : '') + (isSel ? ' is-selectable' : ' is-branch') + (locatedPath === node.path ? ' is-located' : ''),
            'data-path': node.path,
            'data-selectable': isSel ? '1' : '0',
            'aria-expanded': hasKids ? (isOpen ? 'true' : 'false') : undefined,
            'aria-pressed': isSel ? (isChecked ? 'true' : 'false') : undefined,
            title: node.path,
            style: { paddingLeft: 8 + depth * 16 },
            onClick: () => {
              if (isSel) togglePath(node.path);
              else if (hasKids) toggleExpand(node.path);
            },
          },
            hasKids
              ? h('span', { className: 'wf-tree-caret', 'aria-hidden': 'true' }, isOpen ? '▾' : '▸')
              : h('span', { className: 'wf-tree-check', 'aria-hidden': 'true' }, isChecked ? '✓' : ''),
            h('span', { className: 'wf-tree-label' }, node.name || node.path),
            hasKids ? h('span', { className: 'wf-tree-count' }, String(count)) : null,
          ));
          if (hasKids && isOpen) {
            rows.push(...renderNodes(kids, depth + 1));
          }
        }
        return rows;
      }

      const totalSelectable = (treeData && typeof treeData.selectable_total === 'number')
        ? treeData.selectable_total : selectableSet.size;

      return h('div', { className: 'wf-label-left' },
        // 1. 搜索框（只搜当前题型范围内的知识点）
        h('div', { className: 'wf-kp-search-wrap' },
          h('input', {
            className: 'wf-input',
            style: { ...T.input, width: '100%', paddingRight: query ? 28 : 10 },
            value: query,
            'aria-label': '搜索知识点',
            placeholder: mainType ? '搜索「' + mainType + '」可用知识点…' : '输入关键词搜索知识点…',
            onChange: (e) => setQuery(e.target.value),
          }),
          query ? h('button', {
            type: 'button',
            className: 'wf-kp-clear',
            title: '清空搜索',
            'aria-label': '清空搜索',
            onClick: () => setQuery(''),
          }, '✕') : null,
        ),
        // 2. 已选知识点列表
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 5 } },
          h('div', { className: 'wf-kp-section-head' },
            h('span', { style: T.fieldLabel }, '已选知识点（' + paths.length + '）'),
            paths.length > 0 ? h('button', {
              type: 'button',
              onClick: () => onChange([]),
              style: { border: 'none', background: 'transparent', padding: 0, cursor: 'pointer', fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)' },
            }, '清空') : null,
          ),
          h('div', { className: 'wf-kp-chips' },
            paths.length === 0
              ? h('span', { style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } }, '未选择知识点，请在下方目录树点击添加')
              : paths.map((p) => {
                  const outOfScope = scoped && !loading && selectableSet.size > 0 && !selectableSet.has(p);
                  const canLocate = !loading && (selectableSet.has(p) || parentMap.has(p) || countMap.has(p));
                  return h('span', {
                    key: p,
                    className: 'wf-kp-chip' + (outOfScope ? ' is-outside' : '') + (canLocate ? ' is-locatable' : ''),
                    title: outOfScope
                      ? p + '（不在「' + mainType + '」允许范围内）'
                      : (canLocate ? p + '（点击快速定位到下方目录树）' : p),
                    onClick: canLocate ? () => locatePath(p) : undefined,
                  },
                    h('span', { className: 'wf-kp-chip-text' }, p),
                    outOfScope ? h('span', { className: 'wf-kp-chip-warn' }, '超出当前题型') : null,
                    h('button', {
                      type: 'button',
                      className: 'wf-kp-chip-rm',
                      title: '移除',
                      'aria-label': '移除 ' + p,
                      onClick: (e) => {
                        if (e && typeof e.stopPropagation === 'function') e.stopPropagation();
                        removePath(p);
                      },
                    }, '✕'),
                  );
                }),
          ),
        ),
        // 3. 知识点目录树（按当前题型过滤）
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 5 } },
          h('div', { className: 'wf-kp-section-head' },
            h('span', { style: T.fieldLabel }, mainType ? '「' + mainType + '」知识点目录' : '知识点目录'),
            h('span', null, loading ? '加载中…' : (searching ? '匹配 ' + filtered.matchCount + ' 项' : '可选 ' + totalSelectable + ' 项')),
          ),
          h('div', { ref: treeBoxRef, className: 'wf-kp-tree-box wf-scroll', role: 'tree', 'aria-label': '知识点目录树' },
            loading
              ? h('div', { style: { padding: '18px 12px', textAlign: 'center', fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } }, '正在加载知识点目录…')
              : loadError
                ? h('div', { style: { padding: '14px 12px', fontSize: 12, color: 'var(--dsw-alias-state-error-primary)' } }, loadError)
                : visibleItems.length === 0
                  ? h('div', { style: { padding: '18px 12px', textAlign: 'center', fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } }, searching ? '当前题型下未找到匹配知识点' : '该题型暂无可选知识点')
                  : renderNodes(visibleItems, 0),
          ),
        ),
      );
    }

    // ======================================================================
    // 修改标签弹窗（左右两列：左列知识点目录树，右列题型/子类/难度/分类/年份/来源）
    // ======================================================================
    function LabelModal(props) {
      const React = require('react');
      const h = React.createElement;
      const { tax, labels, setL, warnings, busy, saveError, onSave, onClose,
        unitLabel, unitKind, numbers, fetchKnowledgeTree } = props;
      const nums = (numbers || []).map((n) => String(n));
      const labelText = String(unitLabel == null ? '' : unitLabel).trim();
      const isGroup = unitKind === 'group' || nums.length > 1;
      let rangeText = '';
      if (nums.length > 1) {
        const sorted = nums.slice().sort((a, b) => (Number(a) || 0) - (Number(b) || 0));
        rangeText = sorted[0] + '–' + sorted[sorted.length - 1];
      }
      let qTitle = '';
      if (isGroup && rangeText) qTitle = '第 ' + rangeText + ' 题';
      else if (labelText) qTitle = '第 ' + labelText + ' 题';
      else if (nums.length === 1) qTitle = '第 ' + nums[0] + ' 题';
      const qTag = isGroup
        ? qTitle + '（题组·' + nums.length + ' 小题）' : qTitle;
      const subs = (tax.subtypes || {})[labels?.type] || [];
      const diffOpts = (Array.isArray(tax.difficulties) && tax.difficulties.length)
        ? tax.difficulties : ['容易', '适中', '困难'];
      const catOpts = (Array.isArray(tax.categories) && tax.categories.length)
        ? tax.categories : ['优选题', '易错题'];
      const selectedCats = Array.isArray(labels?.categories) ? labels.categories : [];
      return h(ModalShell, {
        title: '修改标签', tag: qTag, width: 820, onClose,
        footer: [
          h('button', { key: 'cancel', className: 'wf-btn', style: T.btn, onClick: onClose }, '取消'),
          h('button', { key: 'save', className: 'wf-btn-primary', style: T.btnPrimary, onClick: onSave, disabled: busy === 'save' }, busy === 'save' ? TEXT.saving : TEXT.saveLabels),
        ],
      },
        h(React.Fragment, null,
          h('div', { className: 'wf-label-modal-grid' },
            // 左列：搜索 + 已选知识点 + 题型限定知识点目录树
            h('div', { role: 'group', 'aria-label': TEXT.knowledge, style: { minWidth: 0 } },
              h(KnowledgePicker, {
                paths: labels?.knowledge || [],
                mainType: labels?.type || '',
                onChange: (arr) => setL('knowledge', arr),
                fetchTree: fetchKnowledgeTree,
              }),
            ),
            // 右列：题型、子类、难度（3按钮）、分类（2按钮）、年份（直接填写）、来源类型
            h('div', { className: 'wf-label-right' },
              formField(TEXT.type, h('select', {
                className: 'wf-input',
                style: { ...T.input, width: '100%' },
                'aria-label': TEXT.type,
                value: labels?.type || '',
                onChange: (e) => { setL('type', e.target.value); setL('subtypes', []); },
              },
                h('option', { value: '' }, '—'),
                (tax.types || []).map((t) => h('option', { key: t, value: t }, t)),
              )),
              formField(TEXT.subtype, h('select', {
                className: 'wf-input',
                style: { ...T.input, width: '100%' },
                'aria-label': TEXT.subtype,
                disabled: !subs.length,
                value: (labels?.subtypes || [])[0] || '',
                onChange: (e) => setL('subtypes', e.target.value ? [e.target.value] : []),
              },
                h('option', { value: '' }, subs.length ? '—' : '无子类'),
                subs.map((s) => h('option', { key: s, value: s }, s)),
              )),
              formField(TEXT.difficulty, h('div', { className: 'wf-seg-btn-row cols-3', role: 'radiogroup', 'aria-label': TEXT.difficulty },
                diffOpts.map((d) => {
                  const active = (labels?.difficulty || '') === d;
                  return h('button', {
                    key: d,
                    type: 'button',
                    role: 'radio',
                    'aria-checked': active ? 'true' : 'false',
                    className: 'wf-seg-btn' + (active ? ' is-active' : ''),
                    onClick: () => setL('difficulty', d),
                  }, d);
                }),
              )),
              formField(TEXT.categories, h('div', { className: 'wf-seg-btn-row cols-2', role: 'group', 'aria-label': TEXT.categories },
                catOpts.map((cat) => {
                  const active = selectedCats.includes(cat);
                  return h('button', {
                    key: cat,
                    type: 'button',
                    'aria-pressed': active ? 'true' : 'false',
                    className: 'wf-seg-btn' + (active ? ' is-active' : ''),
                    onClick: () => {
                      const clean = selectedCats.filter((x) => x && x !== '未填写');
                      const next = active ? clean.filter((x) => x !== cat) : [...clean, cat];
                      setL('categories', next);
                    },
                  }, cat);
                }),
              )),
              formField('年份', h('input', {
                className: 'wf-input',
                style: { ...T.input, width: '100%' },
                'aria-label': '年份',
                value: labels?.year || '',
                placeholder: '如 2026',
                onChange: (e) => setL('year', e.target.value),
              })),
              formField(TEXT.sourceType, h('select', {
                className: 'wf-input',
                style: { ...T.input, width: '100%' },
                'aria-label': TEXT.sourceType,
                value: labels?.source_type || '',
                onChange: (e) => setL('source_type', e.target.value),
              },
                h('option', { value: '' }, '未选择'),
                labels?.source_type && !(tax.sources || []).includes(labels.source_type)
                  ? h('option', { value: labels.source_type, disabled: true }, labels.source_type + '（当前值，请从目录选择）') : null,
                (tax.sources || []).map((source) => h('option', { key: source, value: source }, source)),
              )),
            ),
          ),
          saveError ? h('div', { role: 'alert', style: { ...T.hint, color: 'var(--dsw-alias-state-error-primary)', marginTop: 10 } }, '保存失败：' + saveError) : null,
          (warnings || []).length ? h('div', { style: { ...T.hint, color: 'var(--dsw-alias-state-warn-primary)', marginTop: 10 } }, '提示：' + (warnings || []).join('；')) : null,
        ),
      );
    }

    // 原卷修改弹窗：仅编辑正文/材料（整组共用）+ 逐小题题干/选项/答案；文章大意与解析由下方专用卡片编辑。
    // 组题按每个小题单独折叠（点一题开一题），支持一键展开/收起全部小题。
    function ContentModal(props) {
      const React = require('react');
      const h = React.createElement;
      const { content, detail, onSub, onUnit, onSave, onClose, busy, dirty, saveError } = props;
      const saving = busy === 'content';
      const subs = (content && content.subs) || [];
      const qType = String((detail && detail.type) || (detail && detail.item && detail.item.question_type) || '');
      const isGroup = (detail && (detail.unit_kind === 'group' || (detail.numbers || []).length > 1));
      const noChildStemGroup = isGroup && /(完形|短文填空|选词填空|语法选择|补全对话|教材语篇)/.test(qType);
      const isChoiceType = /(选择|单选|完形|阅读理解|语法选择|语音|听力)/.test(qType);
      const isBlankStem = (val) => {
        const t = String(val || '').replace(/<\/?u[^>]*>/gi, '').replace(/[_\s\u3000]/g, '');
        return !t;
      };
      const [openSubs, setOpenSubs] = React.useState(() => isGroup ? {} : subs.reduce((m, s) => { m[String(s.number)] = true; return m; }, {}));
      const toggleSub = (num) => setOpenSubs((x) => ({ ...x, [String(num)]: !x[String(num)] }));
      const allOpen = subs.length > 0 && subs.every((s) => !!openSubs[String(s.number)]);
      const toggleAllSubs = () => setOpenSubs(allOpen ? {} : subs.reduce((m, s) => { m[String(s.number)] = true; return m; }, {}));
      const nums = ((detail && detail.numbers) || []).map(String);
      const range = nums.length > 1 ? (nums.slice().sort((a, b) => (Number(a) || 0) - (Number(b) || 0)).reduce((acc, n, i, arr) => i === 0 ? n : (i === arr.length - 1 ? acc + '–' + n : acc), '')) : (nums[0] || '');
      const qTag = range ? ('第 ' + range + ' 题' + (isGroup ? '（题组·' + nums.length + ' 小题）' : '')) : '';
      const { fontSize: _size, lineHeight: _height, ...paperInput } = T.input;
      const materialLen = String((content && content.material) || '').length;
      return h(ModalShell, {
        title: '原卷修改', tag: qTag, dirty, width: 760, onClose,
        footer: h('div', { style: { width: '100%' } },
          saveError ? h('div', { role: 'alert', style: { ...T.hint, color: 'var(--dsw-alias-state-error-primary)', marginBottom: 8, overflowWrap: 'anywhere' } }, '保存失败：' + saveError) : null,
          h('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: 8 } },
            h('button', { className: 'wf-btn', style: T.btn, onClick: onClose, disabled: saving }, '取消'),
            h('button', { className: 'wf-btn-primary', style: T.btnPrimary, onClick: onSave, disabled: saving || !dirty }, saving ? '保存中…' : '保存内容'),
          ),
        ),
      },
        h('div', { className: 'wf-cm-body' },
          h('div', { className: 'wf-cm-notice' },
            h('span', { className: 'wf-cm-notice-dot', 'aria-hidden': 'true' }),
            h('span', null, '此处仅修改原卷正文/材料与小题题干、选项、答案（保存为草稿）；文章大意与答案解析请在下方「文章大意 · 解析」卡片中修改。'),
          ),
          h('div', { className: 'wf-cm-section' },
            h('div', { className: 'wf-cm-section-head' },
              h('div', { className: 'wf-cm-section-title' },
                '正文 / 材料',
                h('span', { className: 'wf-cm-section-sub' }, '整组共用 · 含听力原文、阅读原文'),
              ),
              h('span', { className: 'wf-cm-meta' }, materialLen ? materialLen + ' 字' : '空'),
            ),
            h('textarea', {
              className: 'wf-input wf-paper-input',
              value: content.material,
              rows: isGroup ? 9 : 5,
              placeholder: '输入或修改整组共用的正文、阅读材料或听力原文…',
              style: { ...paperInput, width: '100%', resize: 'vertical', minHeight: isGroup ? 170 : 104 },
              onChange: (e) => onUnit('material', e.target.value),
            }),
          ),
          subs.length === 0 ? null : h('div', null,
            h('div', { className: 'wf-cm-section-head', style: { padding: '0 2px' } },
              h('div', { className: 'wf-cm-section-title' },
                '小题内容',
                h('span', { className: 'wf-cm-section-sub' }, '共 ' + subs.length + ' 小题'),
              ),
              subs.length > 1 ? h('button', { type: 'button', className: 'wf-cm-toggle-btn', onClick: toggleAllSubs }, allOpen ? '全部收起' : '全部展开') : null,
            ),
            h('div', { className: 'wf-cm-sub-list' },
              subs.map((s, i) => {
                const open = !!openSubs[String(s.number)];
                const showStem = !noChildStemGroup && (!isGroup || !isBlankStem(s.stem));
                const showOptions = Boolean(String(s.optionsText || '').trim()) || isChoiceType;
                const previewText = showStem ? String(s.stem || '').replace(/\s+/g, ' ').trim().slice(0, 48) : String(s.optionsText || '').replace(/\n+/g, ' ').trim().slice(0, 48);
                const ansPreview = String(s.answer || '').trim();
                return h('div', { key: String(s.number), className: 'wf-cm-sub-card' + (open ? ' is-open' : '') },
                  h('button', { type: 'button', className: 'wf-cm-sub-head', 'aria-expanded': open, onClick: () => toggleSub(s.number) },
                    h('span', { className: 'wf-cm-sub-num' }, s.display_number === null ? '—' : s.number),
                    h('span', { style: { fontSize: 13, fontWeight: 600, flexShrink: 0 } }, s.display_number === null ? '原卷未标小题号' : '第 ' + s.number + ' 题'),
                    h('span', { style: { ...T.hint, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, previewText || '（暂无预览）'),
                    ansPreview ? h('span', { className: 'wf-cm-sub-ans', title: '当前答案：' + ansPreview }, '答 ' + ansPreview) : null,
                    h('span', { className: 'wf-cm-chevron', 'aria-hidden': 'true' }, '▸'),
                  ),
                  open ? h('div', { className: 'wf-cm-sub-body' },
                    showStem ? formField('题干', h('textarea', { className: 'wf-input wf-paper-input', value: s.stem, rows: 2, placeholder: '输入题干内容…', style: { ...paperInput, width: '100%', resize: 'vertical' }, onChange: (e) => onSub(i, 'stem', e.target.value) })) : null,
                    showOptions ? formField('选项（每行一项）', h('textarea', { className: 'wf-input wf-paper-input', value: s.optionsText, rows: 4, placeholder: '每行一项，如 A. xxx', style: { ...paperInput, width: '100%', resize: 'vertical' }, onChange: (e) => onSub(i, 'optionsText', e.target.value) })) : null,
                    formField('答案', h('input', { className: 'wf-input wf-paper-input', value: s.answer, placeholder: '输入参考答案…', style: { ...paperInput, width: '100%' }, onChange: (e) => onSub(i, 'answer', e.target.value) })),
                  ) : null,
                );
              }),
            ),
          ),
        ),
      );
    }

    // ======================================================================
    // 审核页只读内容预览：文章大意 / 解析
    // 与原卷预览并列，数据取 detail.item（与「原卷修改」同一份草稿），只读；
    // 编辑仍走「原卷修改」，这里不回写、不生成、不改上传边界。
    // ======================================================================
    // 占位不是正文：空与规则允许的「无/未提供/未填写」一律按缺失处理。
    const CONTENT_PLACEHOLDERS = new Set(['无', '未提供', '未填写', 'n/a', 'none']);
    function contentText(value) {
      const text = value == null ? '' : String(value).replace(/\r\n?/g, '\n').trim();
      return text && !CONTENT_PLACEHOLDERS.has(text.toLowerCase()) ? text : '';
    }
    // 把 detail 归一成卡片模型：整卷/组题解析、文章大意、逐题解析与缺失统计。
    function contentPreviewModel(detail) {
      const item = (detail && detail.item) || {};
      const subs = Array.isArray(detail && detail.subquestions) ? detail.subquestions : [];
      const byNumber = new Map(subs.filter((s) => s && s.number != null).map((s) => [String(s.number), s]));
      const rows = (Array.isArray(item.subquestions) ? item.subquestions : [])
        .filter((s) => s && s.number != null)
        .map((s) => {
          const sub = byNumber.get(String(s.number)) || {};
          // 无印刷题号只能用内部身份定位并显示「—」，绝不把内部号伪装成印刷题号。
          const display = sub.display_number !== undefined ? sub.display_number : s.number;
          return {
            number: s.number,
            display: display == null ? '—' : String(display),
            unnumbered: display == null,
            answer: contentText(s.answer) || contentText(sub.answer),
            text: contentText(s.explanation),
          };
        });
      const unitText = contentText(item.explanation);
      const summaryText = contentText(item.articleSummary);
      const single = rows.length === 1 ? rows[0] : null;
      const type = String((detail && detail.type) || item.question_type || '');
      // 独立题没有逐题行时，答案挂在解析块标题上，避免为了一个答案再造一行小题。
      const unitAnswer = rows.length ? '' : contentText((subs[0] || {}).answer);
      return {
        summary: summaryText,
        // 整题解析与唯一小题解析逐字相同就只显示一次，避免同一段出现两遍。
        unit: single && unitText === single.text ? '' : unitText,
        rows,
        withText: rows.filter((r) => r.text).length,
        missing: rows.filter((r) => !r.text).length,
        unitAnswer,
        written: /书面表达/.test(type),
        group: String((detail && detail.unit_kind) || item.unit_kind || '') === 'group',
      };
    }
    function ContentPreviewCard(props) {
      const React = require('react');
      const h = React.createElement;
      const { detail, onEdit } = props;
      const model = contentPreviewModel(detail);
      const [parseExpanded, setParseExpanded] = React.useState(false);
      function normalText(text) {
        return h('div', {
          className: 'wf-content-text',
          style: { fontWeight: 400 },
          dangerouslySetInnerHTML: { __html: markLine(String(text || '').replace(/\*\*/g, ''), false) },
        });
      }
      const sectionHead = (title, badge) => h('div', { className: 'wf-content-head' },
        h('span', { className: 'wf-content-title' }, title),
        h('span', { style: { flex: 1 } }),
        badge,
      );
      const summaryIsAi = model.summary && model.summary.includes('（AI生成');
      const summaryBadge = model.summary
        ? h('span', { style: T.badge(summaryIsAi ? 'warn' : '') }, summaryIsAi ? 'AI生成' : '已有')
        : null;
      const unitIsAiTranscript = !!(model.unit && model.unit.includes('（AI转录'));
      const unitIsAiGen = !!(model.unit && model.unit.includes('（AI生成'));
      const aiTranscriptBadge = unitIsAiTranscript
        ? h('span', { style: T.badge('warn'), title: '由录音 AI 转录并去重整理，可能存在识别误差，请人工核对' }, 'AI转录')
        : null;
      const parseBadge = model.rows.length
        ? h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 6 } },
            aiTranscriptBadge,
            h('span', { style: T.badge(''), title: '已填解析的小题数 / 小题总数' },
              '已填 ' + model.withText + '/' + model.rows.length))
        : (model.unit
            ? (aiTranscriptBadge || h('span', { style: T.badge(unitIsAiGen ? 'warn' : '') }, unitIsAiGen ? 'AI生成' : '整题'))
            : null);
      const renderRow = (row) => h('div', {
        key: 'row:' + row.number,
        className: 'wf-content-row',
        style: { padding: '8px 0' },
      },
        h('span', { className: 'wf-content-num', title: row.unnumbered ? '原卷未标小题号' : '第 ' + row.display + ' 题' }, row.display),
        h('div', { className: 'wf-content-row-body', style: { gap: 5 } },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' } },
            row.answer
              ? h('span', { className: 'wf-content-ans', style: { color: 'var(--dsw-alias-label-primary)', fontWeight: 600 }, title: '答案' }, '答案：' + row.answer)
              : h('span', { style: { ...T.hint, fontSize: 11.5 } }, '暂无答案'),
            row.text && row.text.includes('（AI转录')
              ? h('span', { style: { ...T.badge('warn'), fontSize: 10.5, padding: '0 5px' }, title: '由录音 AI 转录并去重整理，可能存在识别误差，请人工核对' }, 'AI转录')
              : (row.text && row.text.includes('（AI生成')
                ? h('span', { style: { ...T.badge('warn'), fontSize: 10.5, padding: '0 5px' } }, 'AI生成')
                : null),
          ),
          row.text
            ? normalText(row.text)
            : h('span', { style: { ...T.hint, fontSize: 12 } }, '暂无解析'),
        ),
      );
      const summarySection = h('div', { key: 'summary', className: 'wf-content-section', style: { marginTop: 6 } },
        sectionHead('文章大意', summaryBadge),
        model.summary
          ? h('div', { className: 'wf-content-quote' }, normalText(model.summary))
          : h('div', { className: 'wf-content-empty', style: T.hint },
              model.written ? '书面表达不要求文章大意。' : '暂无文章大意'),
      );
      const parseSection = h('div', { key: 'parse', className: 'wf-content-section' },
        sectionHead('答案解析', parseBadge),
        model.unit ? h('div', { className: 'wf-content-quote', style: { marginBottom: model.rows.length ? 8 : 0 } },
          h('div', { className: 'wf-content-sub-label', style: { fontWeight: 500 } },
            model.group ? '整组解析' : '本题答案与解析',
            (!model.group && model.unitAnswer)
              ? h('span', { className: 'wf-content-ans', style: { color: 'var(--dsw-alias-label-primary)' }, title: '答案' }, '答案：' + model.unitAnswer)
              : null),
          normalText(model.unit),
        ) : (!model.rows.length && !model.group && model.unitAnswer
          ? h('div', { className: 'wf-content-quote', style: { marginBottom: 6 } },
              h('span', { className: 'wf-content-ans', style: { color: 'var(--dsw-alias-label-primary)' } }, '答案：' + model.unitAnswer))
          : null),
        model.rows.length ? h('div', { className: 'wf-content-rows' },
          (model.rows.length > 1 && !parseExpanded ? model.rows.slice(0, 1) : model.rows).map(renderRow),
          model.rows.length > 1 ? h('div', { style: { display: 'flex', justifyContent: 'flex-start', paddingTop: 6, borderTop: '1px dashed var(--dsw-alias-border-l1)' } },
            h('button', {
              type: 'button',
              className: 'wf-content-link',
              'aria-expanded': parseExpanded,
              onClick: () => setParseExpanded((v) => !v),
            }, parseExpanded ? '收起（仅显示第 1 题）' : '展开其余 ' + (model.rows.length - 1) + ' 题'),
          ) : null,
        ) : null,
        !model.unit && !model.rows.length && (model.group || !model.unitAnswer)
          ? h('div', { className: 'wf-content-empty', style: T.hint },
              model.written ? '书面表达不要求解析。' : '暂无答案解析')
          : null,
      );
      return h('div', { style: { ...T.card, marginTop: 12, marginBottom: 0 } },
        summarySection,
        parseSection,
        onEdit ? h('div', { style: { display: 'flex', justifyContent: 'flex-end', marginTop: 12 } },
          h('button', {
            type: 'button',
            className: 'wf-btn-primary',
            style: T.btnPrimary,
            onClick: onEdit,
          }, '修改'),
        ) : null,
      );
    }

    function ContentEditModal(props) {
      const React = require('react');
      const h = React.createElement;
      const { detail, busy, saveError, onSave, onClose } = props;
      const item = (detail && detail.item) || {};
      const subs = Array.isArray(item.subquestions) ? item.subquestions : [];
      const isGroup = (detail && detail.unit_kind === 'group') || subs.length > 0;
      const initial = React.useMemo(() => ({
        summary: String(item.articleSummary ?? item.article_summary ?? ''),
        unitAns: String(item.answer ?? ''),
        unitExpl: String(item.explanation ?? ''),
        subs: subs.map((s, idx) => ({
          number: s.number !== undefined && s.number !== null ? s.number : idx + 1,
          answer: String(s.answer ?? ''),
          explanation: String(s.explanation ?? ''),
        })),
      }), [detail]);
      const [summary, setSummary] = React.useState(initial.summary);
      const [unitAns, setUnitAns] = React.useState(initial.unitAns);
      const [unitExpl, setUnitExpl] = React.useState(initial.unitExpl);
      const [subEdits, setSubEdits] = React.useState(() => initial.subs.map((row) => ({ ...row })));
      const saving = busy === 'contentEdit';
      const setSub = (idx, key, val) => setSubEdits((prev) => prev.map((row, i) => i === idx ? { ...row, [key]: val } : row));
      const { fontSize: _size, lineHeight: _height, ...paperInput } = T.input;
      const submit = () => {
        const patch = { unit_id: detail.unit_id };
        const expected = {};
        if (summary !== initial.summary) {
          patch.articleSummary = summary;
          expected.articleSummary = initial.summary;
        }
        if (!subs.length) {
          if (unitAns !== initial.unitAns) {
            patch.answer = unitAns;
            expected.answer = initial.unitAns;
          }
          if (unitExpl !== initial.unitExpl) {
            patch.explanation = unitExpl;
            expected.explanation = initial.unitExpl;
          }
        } else {
          if (unitExpl !== initial.unitExpl) {
            patch.explanation = unitExpl;
            expected.explanation = initial.unitExpl;
          }
          const changedSubs = [];
          subEdits.forEach((row, idx) => {
            const orig = initial.subs[idx];
            if (!orig) return;
            const ansChanged = row.answer !== orig.answer;
            const explChanged = row.explanation !== orig.explanation;
            if (!ansChanged && !explChanged) return;
            const subPatch = { number: row.number };
            const subExpected = {};
            if (ansChanged) {
              subPatch.answer = row.answer;
              subExpected.answer = orig.answer;
            }
            if (explChanged) {
              subPatch.explanation = row.explanation;
              subExpected.explanation = orig.explanation;
            }
            subPatch.expected = subExpected;
            changedSubs.push(subPatch);
          });
          if (changedSubs.length) patch.subquestions = changedSubs;
        }
        if (Object.keys(patch).length <= 1) {
          onClose();
          return;
        }
        if (Object.keys(expected).length) patch.expected = expected;
        onSave(patch);
      };
      return h(ModalShell, {
        title: '修改文章大意与答案解析',
        tag: detail?.label ? '第 ' + detail.label + ' 题' : '',
        width: 660,
        onClose: () => { if (!saving) onClose(); },
        footer: h('div', { style: { width: '100%' } },
          saveError ? h('div', { role: 'alert', style: { ...T.hint, color: 'var(--dsw-alias-state-error-primary)', marginBottom: 8 } }, '保存失败：' + saveError) : null,
          h('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: 8 } },
            h('button', { className: 'wf-btn', style: T.btn, onClick: onClose, disabled: saving }, '取消'),
            h('button', { className: 'wf-btn-primary', style: T.btnPrimary, onClick: submit, disabled: saving }, saving ? '保存中…' : '保存'),
          ),
        ),
      },
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 14 } },
          isGroup ? formField('文章大意',
            h('textarea', {
              className: 'wf-input wf-paper-input',
              rows: 3,
              value: summary,
              placeholder: '输入文章大意（原卷没有可留空或填「无」）',
              style: { ...paperInput, width: '100%', resize: 'vertical' },
              onChange: (e) => setSummary(e.target.value),
            })) : null,
          !subs.length ? h(React.Fragment, null,
            formField('答案',
              h('input', {
                className: 'wf-input wf-paper-input',
                value: unitAns,
                style: { ...paperInput, width: '100%' },
                onChange: (e) => setUnitAns(e.target.value),
              })),
            formField('答案解析',
              h('textarea', {
                className: 'wf-input wf-paper-input',
                rows: 4,
                value: unitExpl,
                style: { ...paperInput, width: '100%', resize: 'vertical' },
                onChange: (e) => setUnitExpl(e.target.value),
              })),
          ) : h('div', { style: { display: 'flex', flexDirection: 'column', gap: 10 } },
            h('span', { style: T.fieldLabel }, '各小题答案与解析'),
            subEdits.map((row, idx) => h('div', {
              key: String(row.number),
              style: { padding: '10px 12px', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 8, background: 'var(--dsw-alias-bg-layer-2)', display: 'flex', flexDirection: 'column', gap: 8 },
            },
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 8 } },
                h('span', { className: 'wf-content-num' }, String(row.number)),
                h('span', { style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)', flexShrink: 0 } }, '答案'),
                h('input', {
                  className: 'wf-input wf-paper-input',
                  value: row.answer,
                  style: { ...paperInput, flex: 1, minWidth: 0 },
                  onChange: (e) => setSub(idx, 'answer', e.target.value),
                }),
              ),
              h('textarea', {
                className: 'wf-input wf-paper-input',
                rows: 2,
                value: row.explanation,
                placeholder: '输入该小题答案解析',
                style: { ...paperInput, width: '100%', resize: 'vertical' },
                onChange: (e) => setSub(idx, 'explanation', e.target.value),
              }),
            )),
          ),
        ),
      );
    }

    function PaperPreviewModal(props) {
      const React = require('react');
      const h = React.createElement;
      const { batchName, data, loading, error, onClose, onReload } = props;
      const [showKey, setShowKey] = React.useState(false);

      const formatRich = (text) => {
        let s = String(text || '').replace(/\r\n?/g, '\n');
        // Strip stray unmatched bold markers like leading "**______"
        s = s.replace(/\*\*([^*]*?)(?=\n|$)/g, (m, inner) => (m.endsWith('**') ? m : inner));
        // Restore paragraph/cell leading indent wrongly normalized into ______
        s = s.replace(/(^|\n|\|\s*)_{4,}(?=[A-Z][a-z]|[“"‘'《\u4e00-\u9fff])/g, '$1\u3000\u3000');
        let html = markLine(s, false);
        // Symbol/short marker wrapped by underscores and <u>, e.g. __<u>▲</u>__
        html = html.replace(/[_\uff3f]{2,}\s*<u>([^<]{1,4})<\/u>\s*[_\uff3f]{2,}/g, '<span class="wf-blank">$1</span>');
        // Underlined underscores or blank spaces -> unified fill-blank rule (avoid double lines)
        html = html.replace(/<u>[\s\u3000\xa0]*[_\uff3f]{2,}[\s\u3000\xa0]*<\/u>/g, '<span class="wf-fill-blank"></span>');
        html = html.replace(/<u>[\s\u3000\xa0]{2,}<\/u>/g, '<span class="wf-fill-blank"></span>');
        // Standalone answer-area underline on its own line gets full response width
        html = html.replace(/(^|\n)[ \t]*[_\uff3f]{3,}[ \t]*(?=\n|$)/g, '$1<span class="wf-fill-line"></span>');
        // Render inline underscore runs (______ ) as clean Word-style baseline underline rules
        html = html.replace(/[_\uff3f]{3,}/g, '<span class="wf-fill-blank"></span>');
        return html;
      };

      const renderTextWithImages = (text, keyPrefix, style, imgStyle) => {
        const s = String(text || '').replace(/\r\n?/g, '\n');
        if (!s.trim()) return null;
        const imgRe = /\[IMG\s+([^\]]+)\]/gi;
        const nodes = [];
        let last = 0;
        let m;
        let seq = 0;
        while ((m = imgRe.exec(s)) !== null) {
          if (m.index > last) {
            const chunk = s.slice(last, m.index);
            if (chunk.trim()) {
              nodes.push(h('span', { key: keyPrefix + ':s' + (seq++), dangerouslySetInnerHTML: { __html: formatRich(chunk) } }));
            }
          }
          const fname = String(m[1] || '').trim().split('/').pop();
          const url = '/api/batches/' + encodeURIComponent(batchName) + '/media/' + encodeURIComponent(fname);
          nodes.push(h('span', { key: keyPrefix + ':i' + (seq++), style: { display: imgStyle && imgStyle.display === 'block' ? 'block' : 'inline-block', verticalAlign: 'middle', margin: '4px 6px' } },
            h(PreviewImage, { url, name: fname, style: imgStyle })));
          last = imgRe.lastIndex;
        }
        if (last < s.length) {
          const tail = s.slice(last);
          if (tail.trim()) {
            nodes.push(h('span', { key: keyPrefix + ':s' + (seq++), dangerouslySetInnerHTML: { __html: formatRich(tail) } }));
          }
        }
        if (!nodes.length) return null;
        return h('div', { key: keyPrefix, style: style || { fontSize: 13.5, lineHeight: 1.8, whiteSpace: 'pre-wrap' } }, nodes);
      };

      const isImageAtt = (a) => {
        if (!a || !(a.url || a.name)) return false;
        const kind = String(a.kind || '').toLowerCase();
        if (kind === 'audio' || kind === 'video') return false;
        const target = String(a.name || a.url || '').toLowerCase().split('?')[0];
        if (/\.(mp3|wav|m4a|ogg|aac|flac|mp4|webm)$/i.test(target)) return false;
        return kind === 'image' || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(target) || !kind;
      };

      const attPreviewUrl = (a) => {
        const raw = String((a && a.url) || '').trim();
        if (/^(?:\/api\/|\/wf-plugin\/|https?:|blob:|data:image\/)/i.test(raw)) return raw;
        const fname = String((a && (a.name || a.url)) || '').split('/').pop();
        return '/api/batches/' + encodeURIComponent(batchName) + '/media/' + encodeURIComponent(fname);
      };

      const optionKeyOf = (o) => {
        if (!o) return '';
        if (typeof o === 'object') return String(o.key || o.label || '').trim().toUpperCase();
        const m = String(o).trim().match(/^([A-H])(?:[\.．、\s]|$)/i);
        return m ? m[1].toUpperCase() : '';
      };

      const inlineImgNamesOf = (blocks) => {
        const names = new Set();
        (Array.isArray(blocks) ? blocks : []).forEach((blk) => {
          (Array.isArray(blk && blk.rows) ? blk.rows : []).forEach((r) => {
            (Array.isArray(r && r.cells) ? r.cells : []).forEach((c) => {
              const txt = String((c && c.text) || '');
              const re = /\[IMG\s+([^\]]+)\]/gi;
              let m;
              while ((m = re.exec(txt)) !== null) {
                names.add(String(m[1] || '').trim().split('/').pop());
              }
            });
          });
        });
        return names;
      };

      const renderAttachments = (attachments, keyPrefix, allowAnswer, options, materialBlocks) => {
        const list = Array.isArray(attachments) ? attachments.filter(isImageAtt) : [];
        const optKeys = new Set((Array.isArray(options) ? options : []).map(optionKeyOf).filter(Boolean));
        const inlineNames = inlineImgNamesOf(materialBlocks);
        const visible = list.filter((a) => {
          const p = String(a.placement || 'question').toLowerCase();
          if (p === 'answer' || p === 'explanation') return !!allowAnswer;
          if (p === 'option' && a.optionKey && optKeys.has(String(a.optionKey).trim().toUpperCase())) return false;
          const fname = String(a.name || a.url || '').split('/').pop();
          if (fname && inlineNames.has(fname)) return false;
          return true;
        });
        if (!visible.length) return null;
        return h('div', { key: keyPrefix, style: { display: 'flex', flexWrap: 'wrap', gap: 10, margin: '6px 0 8px' } },
          visible.map((a, idx) => {
            const fname = String(a.name || a.url || '').split('/').pop();
            const url = attPreviewUrl(a);
            return h('div', { key: keyPrefix + ':' + idx, style: { maxWidth: 260 } },
              h(PreviewImage, { url, name: fname, style: { maxWidth: 240, maxHeight: 180, objectFit: 'contain' } }));
          }),
        );
      };

      const renderTableElement = (blk, key) => {
        const rows = Array.isArray(blk && blk.rows) ? blk.rows : [];
        if (!rows.length) return null;
        return h('table', {
          key,
          style: { width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed', margin: '8px 0', fontSize: 13, background: 'var(--dsw-alias-bg-layer-1)' },
        },
          h('tbody', null, rows.map((r, ri) => h('tr', { key: ri },
            (r.cells || []).map((c, ci) => h(c.header ? 'th' : 'td', {
              key: ci,
              rowSpan: c.rowspan > 1 ? c.rowspan : undefined,
              colSpan: c.colspan > 1 ? c.colspan : undefined,
              style: { border: '1px solid var(--dsw-alias-border-l2)', padding: '6px 9px', textAlign: 'left', verticalAlign: 'top', wordBreak: 'break-word', whiteSpace: 'pre-wrap', fontWeight: c.header ? 600 : 400 },
            }, renderTextWithImages(c.text || '', key + ':c:' + ri + ':' + ci, { fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap' }, { maxWidth: 160, maxHeight: 120, objectFit: 'contain', display: 'block', margin: '4px 0' }))),
          ))),
        );
      };

      const parseFallbackTable = (inner) => {
        const lines = String(inner || '').replace(/<br\s*\/?>/gi, '\n').split('\n').map((l) => l.trim()).filter(Boolean);
        if (!lines.length) return null;
        const hasPipes = lines.some((l) => l.includes('|'));
        const rows = hasPipes
          ? lines.map((l) => ({ cells: l.split('|').map((cell) => ({ text: cell.trim(), rowspan: 1, colspan: 1 })) }))
          : [{ cells: [{ text: lines.join('\n'), rowspan: 1, colspan: 1 }] }];
        return { rows };
      };

      const renderRichBlocks = (text, materialBlocks, usedBlocks, keyPrefix) => {
        const raw = String(text || '').replace(/\r\n?/g, '\n');
        if (!raw.trim()) return null;
        const re = /\[TABLE(?:\s+(\d+))?\]([\s\S]*?)\[\/TABLE\]/gi;
        const parts = [];
        let lastIdx = 0;
        let match;
        let seq = 0;
        while ((match = re.exec(raw)) !== null) {
          if (match.index > lastIdx) {
            const seg = raw.slice(lastIdx, match.index).trim();
            if (seg) {
              parts.push(renderTextWithImages(seg, keyPrefix + ':t:' + (seq++), { fontSize: 13.5, lineHeight: 1.8, whiteSpace: 'pre-wrap', marginBottom: 4 }));
            }
          }
          const tblNum = match[1] ? Number(match[1]) - 1 : -1;
          let matchedIdx = (tblNum >= 0 && Array.isArray(materialBlocks) && materialBlocks[tblNum]) ? tblNum : -1;
          if (matchedIdx < 0 && Array.isArray(materialBlocks)) {
            matchedIdx = materialBlocks.findIndex((b, idx) => b && Array.isArray(b.rows) && (!usedBlocks || !usedBlocks.has(idx)));
          }
          let blk = matchedIdx >= 0 ? materialBlocks[matchedIdx] : null;
          if (blk && usedBlocks) usedBlocks.add(matchedIdx);
          if (!blk || !Array.isArray(blk.rows) || !blk.rows.some((r) => (r.cells || []).some((c) => String(c.text || '').trim()))) {
            const parsed = parseFallbackTable(match[2]);
            if (parsed) blk = parsed;
          }
          if (blk) parts.push(renderTableElement(blk, keyPrefix + ':tbl:' + (seq++)));
          lastIdx = re.lastIndex;
        }
        if (lastIdx < raw.length) {
          const tail = raw.slice(lastIdx).trim();
          if (tail) {
            parts.push(renderTextWithImages(tail, keyPrefix + ':t:' + (seq++), { fontSize: 13.5, lineHeight: 1.8, whiteSpace: 'pre-wrap' }));
          }
        }
        return parts.length ? parts : null;
      };

      const renderOptionsGrid = (options, materialBlocks, usedBlocks, keyPrefix, attachments) => {
        const rawList = Array.isArray(options) ? options : [];
        const list = [];
        const trailingBlocks = [];
        rawList.forEach((o, idx) => {
          if (!o) return;
          if (typeof o === 'string') {
            let s = o.trim();
            const splitIdx = s.search(/\n(?=(?:任务[一二三四五六七八九十\d]|\[TABLE))/);
            if (splitIdx >= 0) {
              trailingBlocks.push(s.slice(splitIdx + 1).trim());
              s = s.slice(0, splitIdx).trim();
            }
            if (s) list.push({ key: '', text: s, optKey: optionKeyOf(s) });
          } else if (typeof o === 'object' && (o.key || o.text)) {
            const k = String(o.key || '');
            list.push({ key: k, text: String(o.text || ''), optKey: optionKeyOf(o) });
          }
        });
        if (!list.length && !trailingBlocks.length) return null;
        const optImgs = {};
        (Array.isArray(attachments) ? attachments : []).forEach((a) => {
          if (!isImageAtt(a)) return;
          if (String(a.placement || '').toLowerCase() === 'option' && a.optionKey) {
            const k = String(a.optionKey).trim().toUpperCase();
            if (!optImgs[k]) optImgs[k] = [];
            optImgs[k].push(a);
          }
        });
        const hasOptImgs = list.some((o) => o.optKey && optImgs[o.optKey]);
        const maxLen = list.reduce((m, o) => Math.max(m, o.text.length + (o.key ? o.key.length + 2 : 0)), 0);
        const cols = hasOptImgs ? (list.length <= 4 ? Math.max(1, list.length) : 2)
          : (maxLen <= 16 && list.length === 4 ? 4 : (maxLen <= 34 ? 2 : 1));
        return h(React.Fragment, null,
          list.length ? h('div', {
            style: {
              display: 'grid',
              gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
              gap: hasOptImgs ? '8px 14px' : '4px 16px',
              marginTop: 4,
              paddingLeft: 18,
              alignItems: 'center',
            },
          }, list.map((o, idx) => {
            const imgs = (o.optKey && optImgs[o.optKey]) || [];
            const labelHtml = formatRich((o.key ? o.key + '. ' : '') + o.text);
            if (!imgs.length) {
              return h('div', {
                key: String(o.key || idx),
                style: { fontSize: 13.5, lineHeight: 1.7, overflowWrap: 'anywhere' },
                dangerouslySetInnerHTML: { __html: labelHtml },
              });
            }
            return h('div', {
              key: String(o.key || idx),
              style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13.5, lineHeight: 1.7 },
            },
              h('span', { style: { flexShrink: 0 }, dangerouslySetInnerHTML: { __html: labelHtml } }),
              imgs.map((a, ai) => h(PreviewImage, {
                key: 'oimg:' + ai,
                url: attPreviewUrl(a),
                name: String(a.name || a.url || '').split('/').pop(),
                style: { maxWidth: 110, maxHeight: 76, objectFit: 'contain', borderRadius: 4, flexShrink: 0 },
              })),
            );
          })) : null,
          trailingBlocks.map((tb, ti) => h('div', { key: 'tb:' + ti, style: { marginTop: 8 } },
            renderRichBlocks(tb, materialBlocks, usedBlocks, keyPrefix + ':tb:' + ti))),
        );
      };

      const renderMaterialBlocks = (blocks, usedBlocks) => {
        if (!Array.isArray(blocks) || !blocks.length) return null;
        return blocks.map((blk, bi) => {
          if (usedBlocks && usedBlocks.has(bi)) return null;
          return renderTableElement(blk, 'blk:' + bi);
        });
      };

      const renderItems = (items) => {
        const cleanHeading = (s) => String(s || '').replace(/\[IMG\s+[^\]]+\]/gi, '').replace(/\*{2,}/g, '').replace(/\s+/g, ' ').trim();
        const cleanBandTitle = (s) => {
          let out = cleanHeading(s);
          while (out.length >= 2 && (
            (out[0] === '【' && out[out.length - 1] === '】') ||
            (out[0] === '[' && out[out.length - 1] === ']') ||
            (out[0] === '(' && out[out.length - 1] === ')') ||
            (out[0] === '（' && out[out.length - 1] === '）')
          )) {
            out = out.slice(1, -1).trim();
          }
          return out;
        };
        const normText = (s) => cleanHeading(s).replace(/[\s，,。．.；;：:（）()【】\[\]《》“”"'\-—_]+/g, '').toLowerCase();
        const normEq = (a, b) => {
          const na = normText(a);
          const nb = normText(b);
          return Boolean(na && nb && na === nb);
        };
        const joinNumberedStem = (num, stem, allowPrefix) => {
          const s = String(stem || '').trim();
          const nStr = (num !== null && num !== undefined) ? String(num).trim() : '';
          if (!allowPrefix || !nStr) return s;
          if (!s) return nStr + '.';
          const escaped = nStr.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          if (new RegExp('^' + escaped + '(?:[\\.．、\\)）]\\s*|$)').test(s)) return s;
          return nStr + '. ' + s;
        };
        let lastPart = '';
        let lastBand = '';
        let lastSection = '';
        let lastQType = '';
        let lastGroupStem = '';
        return (items || []).map((it, idx) => {
          if (!it || typeof it !== 'object') return null;
          const secObj = (it.source_section && typeof it.source_section === 'object') ? it.source_section : {};
          const partTitle = cleanHeading(secObj.part || '');
          const bandTitle = cleanBandTitle(secObj.band || (secObj.lesson_band && secObj.lesson_band.label) || '');
          const secTitle = cleanHeading(secObj.title || '');
          const secInst = cleanHeading(secObj.instruction || '');
          const qType = cleanHeading(it.question_type || '');
          const partChanged = Boolean(partTitle && partTitle !== lastPart && partTitle.length <= 80);
          if (partChanged) { lastPart = partTitle; lastBand = ''; lastSection = ''; lastGroupStem = ''; }
          const bandChanged = Boolean(bandTitle && bandTitle !== lastBand && bandTitle.length <= 80);
          if (bandChanged) { lastBand = bandTitle; lastSection = ''; lastGroupStem = ''; }
          const secChanged = Boolean(secTitle && secTitle !== lastSection && secTitle.length <= 80);
          if (secChanged) { lastSection = secTitle; lastGroupStem = ''; }
          const qTypeChanged = Boolean(!secTitle && qType && lastQType && qType !== lastQType);
          if (qTypeChanged) lastGroupStem = '';
          if (qType) lastQType = qType;
          const topGap = idx > 0 ? ((partChanged || bandChanged) ? 28 : (secChanged ? 24 : (qTypeChanged ? 22 : 0))) : 0;

          const subs = Array.isArray(it.subquestions) ? it.subquestions : [];
          const isGroup = it.unit_kind === 'group' || subs.length > 0;
          const unitNum = it.original_number !== undefined && it.original_number !== null ? it.original_number : (it.number != null ? it.number : (Array.isArray(it.numbers) && it.numbers.length === 1 ? it.numbers[0] : null));
          const usedBlocks = new Set();
          const unitKey = String(it.unit_id || it.id || idx);
          const rawGroupStem = String(it.stem || '').trim();
          const normGroupStem = normText(rawGroupStem);
          const showGroupStem = Boolean(
            isGroup && rawGroupStem
            && !normEq(rawGroupStem, secTitle)
            && !normEq(rawGroupStem, secInst)
            && (!normGroupStem || normGroupStem !== lastGroupStem),
          );
          if (isGroup) {
            if (normGroupStem) lastGroupStem = normGroupStem;
          } else {
            lastGroupStem = '';
          }
          const showSecInst = Boolean(secChanged && secInst && !normEq(secInst, secTitle));

          return h('div', { key: unitKey, style: { marginTop: topGap, marginBottom: 14 } },
            partChanged ? h('div', {
              style: {
                fontWeight: 700,
                fontSize: 15,
                textAlign: 'center',
                margin: '0 0 10px',
                color: 'var(--dsw-alias-label-primary)',
                letterSpacing: '0.5px',
              },
            }, partTitle) : null,
            bandChanged ? h('div', {
              style: {
                display: 'inline-block',
                fontWeight: 700,
                fontSize: 14.5,
                padding: '4px 12px',
                margin: (partChanged ? '4px' : '0') + ' 0 10px',
                borderRadius: 6,
                background: 'rgba(37, 99, 235, 0.10)',
                color: '#1d4ed8',
                borderLeft: '3px solid #2563eb',
                letterSpacing: '0.3px',
              },
            }, bandTitle) : null,
            secChanged ? h('div', {
              style: {
                fontWeight: 700,
                fontSize: 14.5,
                margin: ((partChanged || bandChanged) ? '4px' : '0') + ' 0 8px',
                color: 'var(--dsw-alias-label-primary)',
                borderBottom: '1px solid var(--dsw-alias-border-l1)',
                paddingBottom: 4,
              },
            }, secTitle) : null,
            showSecInst ? h('div', {
              style: {
                fontSize: 13,
                lineHeight: 1.75,
                color: 'var(--dsw-alias-label-secondary)',
                margin: '0 0 10px',
                textIndent: '2em',
              },
            }, secInst) : null,
            isGroup ? h('div', null,
              showGroupStem ? h('div', { style: { fontWeight: 600, marginBottom: 4 } }, renderRichBlocks(it.stem, it.materialBlocks, usedBlocks, unitKey + ':stem')) : null,
              it.material ? h('div', { style: { marginBottom: 8 } }, renderRichBlocks(it.material, it.materialBlocks, usedBlocks, unitKey + ':mat')) : null,
              renderAttachments(it.attachments, unitKey + ':att', showKey, it.options, it.materialBlocks),
              Array.isArray(it.options) && it.options.length ? h('div', { style: { marginBottom: 8, padding: '6px 10px', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 6, background: 'var(--dsw-alias-bg-layer-2)' } },
                renderOptionsGrid(it.options, it.materialBlocks, usedBlocks, unitKey + ':gopts', it.attachments),
              ) : null,
              showKey && it.articleSummary ? h('div', { style: { fontSize: 12.5, color: 'var(--dsw-alias-label-secondary)', marginBottom: 6, padding: '4px 8px', background: 'var(--dsw-alias-bg-layer-2)', borderRadius: 6 } }, '【文章大意】' + it.articleSummary) : null,
              subs.map((sub, si) => {
                const sNum = sub.original_number !== undefined && sub.original_number !== null ? sub.original_number : (sub.number != null ? sub.number : (si + 1));
                const sStem = String(sub.stem || '').trim();
                const hasOpts = Array.isArray(sub.options) && sub.options.length > 0;
                const blankOnly = !sStem || /^[\s_]+$/.test(sStem);
                const subAtts = renderAttachments(sub.attachments, unitKey + ':s' + si + ':att', showKey, sub.options);
                if (blankOnly && !hasOpts && !subAtts) {
                  return showKey && (sub.answer || sub.explanation)
                    ? h('div', { key: String(sNum) + ':' + si, style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)', paddingLeft: 18, marginBottom: 4 } },
                        String(sNum) + '. ' + (sub.answer ? '答案：' + sub.answer + '　' : '') + (sub.explanation ? '解析：' + sub.explanation : ''))
                    : null;
                }
                return h('div', { key: String(sNum) + ':' + si, style: { marginBottom: 8 } },
                  blankOnly && hasOpts
                    ? h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap', fontSize: 13.5, lineHeight: 1.75 } },
                        h('span', { style: { fontWeight: 600, flexShrink: 0 } }, String(sNum) + '.'),
                        h('div', { style: { flex: 1, minWidth: 0 } }, renderOptionsGrid(sub.options, it.materialBlocks, usedBlocks, unitKey + ':s' + si, sub.attachments)),
                      )
                    : h('div', null,
                        !blankOnly ? renderRichBlocks(joinNumberedStem(sNum, sStem, true), it.materialBlocks, usedBlocks, unitKey + ':s' + si + ':stem') : null,
                        subAtts,
                        renderOptionsGrid(sub.options, it.materialBlocks, usedBlocks, unitKey + ':s' + si + ':opts', sub.attachments),
                      ),
                  showKey && (sub.answer || sub.explanation)
                    ? h('div', { style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)', paddingLeft: 18, marginTop: 2 } },
                        (sub.answer ? '答案：' + sub.answer + '　' : '') + (sub.explanation ? '解析：' + sub.explanation : ''))
                    : null,
                );
              }),
              renderMaterialBlocks(it.materialBlocks, usedBlocks),
            ) : h('div', null,
              renderRichBlocks(joinNumberedStem(unitNum, it.stem, it.printed_number !== false), it.materialBlocks, usedBlocks, unitKey + ':stem'),
              renderAttachments(it.attachments, unitKey + ':att', showKey, it.options, it.materialBlocks),
              renderOptionsGrid(it.options, it.materialBlocks, usedBlocks, unitKey + ':opts', it.options ? it.attachments : null),
              renderMaterialBlocks(it.materialBlocks, usedBlocks),
              showKey && (it.answer || it.explanation)
                ? h('div', { style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)', paddingLeft: 18, marginTop: 2 } },
                    (it.answer ? '答案：' + it.answer + '　' : '') + (it.explanation ? '解析：' + it.explanation : ''))
                : null,
            ),
          );
        });
      };

      const tags = [];
      if (data && data.source_type && data.source_type !== '未填写') tags.push({ k: '来源', v: data.source_type });
      if (data && data.year && data.year !== '未填写') tags.push({ k: '年份', v: data.year });
      if (data && Array.isArray(data.categories) && data.categories.length) tags.push({ k: '分类', v: data.categories.join(' / ') });

      return h(ModalShell, {
        title: '整卷预览（Word 排版视图）',
        tag: data && Array.isArray(data.items) ? ('共 ' + data.items.length + ' 组/题') : '',
        width: 860,
        onClose,
        footer: h('div', { style: { display: 'flex', alignItems: 'center', width: '100%', gap: 8 } },
          h('button', { type: 'button', className: 'wf-btn', style: T.btn, onClick: () => setShowKey((v) => !v) }, showKey ? '隐藏答案与解析' : '显示答案与解析'),
          h('span', { style: { flex: 1 } }),
          onReload ? h('button', { type: 'button', className: 'wf-btn', style: T.btn, onClick: onReload, disabled: loading }, '刷新') : null,
          h('button', { type: 'button', className: 'wf-btn-primary', style: T.btnPrimary, onClick: onClose }, '关闭'),
        ),
      },
        loading ? h('div', { style: { ...T.hint, padding: '24px 0', textAlign: 'center' } }, '正在加载整卷排版…')
          : (error ? h('div', { style: { color: 'var(--dsw-alias-state-error-primary)', padding: 12 } }, '加载失败：' + error)
            : h('div', {
                className: 'wf-paper',
                style: {
                  background: 'var(--dsw-alias-bg-layer-2)',
                  border: '1px solid var(--dsw-alias-border-l1)',
                  borderRadius: 8,
                  padding: '24px 30px',
                  fontFamily: '"Times New Roman","SimSun","Songti SC",serif',
                  color: 'var(--dsw-alias-label-primary)',
                },
              },
                h('div', { style: { textAlign: 'center', marginBottom: 16, paddingBottom: 12, borderBottom: '2px solid var(--dsw-alias-border-l2)' } },
                  h('div', { style: { fontSize: 18, fontWeight: 700, lineHeight: 1.5 } }, (data && data.title) || batchName),
                  tags.length ? h('div', { style: { display: 'flex', justifyContent: 'center', gap: 8, flexWrap: 'wrap', marginTop: 8 } },
                    tags.map((t) => h('span', {
                      key: t.k,
                      style: { fontSize: 11.5, padding: '2px 9px', borderRadius: 999, border: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-alias-bg-layer-1)', color: 'var(--dsw-alias-label-secondary)', fontFamily: 'sans-serif' },
                    }, t.k + '：' + t.v)),
                  ) : null,
                ),
                (data && Array.isArray(data.items) && data.items.length)
                  ? renderItems(data.items)
                  : h('div', { style: { ...T.hint, textAlign: 'center', padding: 20 } }, '暂无已组装题目内容'),
              )),
      );
    }

    // ======================================================================
    // 审阅视图（原工作台三栏：单元列表 + 工作区原文/小题/标签/待人工）
    // ======================================================================
    function ReviewView(props) {
      const React = require('react');
      const h = React.createElement;
      const { batchName, unitId, rows } = props;
      const onSelectUnit = props.onSelectUnit || (() => {});
      const cacheKey = String(batchName || '') + '\0' + String(unitId || '');
      const initialCached = getCachedUnit(batchName, unitId);
      const [detailPair, setDetailPair] = React.useState(() => initialCached ? { key: cacheKey, data: initialCached } : null);
      const detail = (detailPair && detailPair.key === cacheKey) ? detailPair.data : getCachedUnit(batchName, unitId);
      const [labels, setLabels] = React.useState(() => initialCached ? (initialCached.labels || {}) : null);
      const [busy, setBusy] = React.useState('');
      const [err, setErr] = React.useState('');
      const [filter, setFilter] = React.useState('all'); // all | open | blocked | unverified
      const [q, setQ] = React.useState('');
      const [labelDlg, setLabelDlg] = React.useState(false);
      const [labelError, setLabelError] = React.useState('');
      const [labelNotice, setLabelNotice] = React.useState('');
      const [content, setContent] = React.useState(() => initialCached ? makeContentSnapshot(initialCached) : null);
      const [contentOriginal, setContentOriginal] = React.useState(() => initialCached ? makeContentSnapshot(initialCached) : null);
      const [contentDlg, setContentDlg] = React.useState(false); // 「原卷修改」弹窗
      const [contentError, setContentError] = React.useState('');
      const [aiBusy, setAiBusy] = React.useState('');
      const [aiNotice, setAiNotice] = React.useState('');
      const [aiModal, setAiModal] = React.useState(null);
      const [aiModalOpen, setAiModalOpen] = React.useState(false);
      const [contentEditDlg, setContentEditDlg] = React.useState(false);
      const [contentEditError, setContentEditError] = React.useState('');
      const [paperDlg, setPaperDlg] = React.useState(false);
      const [paperData, setPaperData] = React.useState(null);
      const [paperLoading, setPaperLoading] = React.useState(false);
      const [paperError, setPaperError] = React.useState('');
      const contentSave = React.useRef(null);
      const contentPatch = React.useMemo(() => buildContentPatch(content, contentOriginal), [content, contentOriginal]);
      const contentDirty = !!contentPatch;

      const requestGate = React.useRef(null);
      if (!requestGate.current) requestGate.current = createRequestGate();
      const mounted = React.useRef(false);
      const listScrollRef = React.useRef(null);
      const applyDetail = React.useCallback((d) => {
        setDetailPair(d ? { key: cacheKey, data: d } : null);
        setLabels(d ? (d.labels || {}) : null);
        if (d) initContent(d);
        else { setContent(null); setContentOriginal(null); }
      }, [cacheKey]);
      const load = React.useCallback(async (opts) => {
        if (!mounted.current) return;
        if (!unitId || unitId === '_') return; // 等待真实首题 ID，避免无意义的 /units/_ 404 往返
        const force = !!(opts && opts.force);
        const silent = !!(opts && opts.silent);
        const request = requestGate.current.begin();
        if (!silent) setErr('');
        if (!force) {
          const cached = getCachedUnit(batchName, unitId);
          if (cached) {
            applyDetail(cached);
            setErr('');
            return;
          }
          // 不阻塞在整卷 units_bundle 上：后台并行预取整卷，前台直拉当前单题（~85ms 秒出标签与差异）。
          loadBatchUnitsBundle(batchName).catch(() => {});
        }
        // 冷启动/网络抖动时单次失败会让题目区空白：对可重试错误做几次短退避重试，
        // 每次都以 requestGate 的 current() 为准，切题即作废旧请求。
        const delays = [0, 500, 1200, 2400];
        let lastErr = null;
        for (let attempt = 0; attempt < delays.length; attempt++) {
          if (!request.current()) return;
          if (delays[attempt]) await new Promise((r) => setTimeout(r, delays[attempt]));
          if (!request.current()) return;
          try {
            const d = await jget('/batches/' + encodeURIComponent(batchName) + '/units/' + encodeURIComponent(unitId), request.signal);
            if (!request.current()) return;
            setCachedUnit(batchName, unitId, d);
            applyDetail(d);
            setErr('');
            return;
          } catch (e) {
            if (!request.current()) return;
            lastErr = e;
            const msg = String((e && e.message) || e);
            if (/\b(400|401|403|404)\b/.test(msg)) break; // 非瞬时错误，不重试
          }
        }
        if (request.current() && !silent) setErr(String((lastErr && lastErr.message) || lastErr || '加载失败'));
      }, [batchName, unitId, applyDetail]);
      // 进卷即后台预加载整卷全部单元与题图（当前题优先，音频按需不预取）。
      React.useEffect(() => {
        let cancelled = false;
        loadBatchUnitsBundle(batchName).then((entry) => {
          if (cancelled || !entry) return;
          const cur = entry.units.get(String(unitId));
          const orderedUrls = [];
          if (cur) orderedUrls.push(...collectBlockImageUrls(cur.preview_blocks));
          for (const uid of (entry.order || [])) {
            if (String(uid) === String(unitId)) continue;
            const u = entry.units.get(String(uid));
            if (u) orderedUrls.push(...collectBlockImageUrls(u.preview_blocks));
          }
          prefetchImages(orderedUrls, 4);
        }).catch(() => {});
        return () => { cancelled = true; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, [batchName]);
      React.useEffect(() => {
        mounted.current = true;
        const cached = getCachedUnit(batchName, unitId);
        if (cached) {
          applyDetail(cached);
          setErr('');
        } else {
          load();
        }
        return () => { mounted.current = false; requestGate.current.cancel(); };
      }, [batchName, unitId, applyDetail, load]);
      React.useEffect(() => { setLabelNotice(''); setLabelError(''); setLabelDlg(false); setContentDlg(false); setAiNotice(''); setContentEditDlg(false); setContentEditError(''); setBusy(''); setErr(''); }, [batchName, unitId]);
      // 后台/命令行完成审核后及时读回结果；编辑期间暂停，避免覆盖未保存内容。
      React.useEffect(() => {
        if (contentDirty || labelDlg || contentDlg || busy) return;
        const refresh = () => {
          if (typeof document === 'undefined' || document.visibilityState !== 'hidden') load({ force: true, silent: true });
        };
        const timer = setInterval(refresh, 8000);
        if (typeof window !== 'undefined') window.addEventListener('focus', refresh);
        return () => {
          clearInterval(timer);
          if (typeof window !== 'undefined') window.removeEventListener('focus', refresh);
        };
      }, [load, contentDirty, labelDlg, contentDlg, busy]);
      // 媒体 origin 异步到达后触发一次重渲染，使图片/音频切到绝对 URL。
      const [, _bumpOrigin] = React.useState(0);
      React.useEffect(() => { ensureMediaOrigin().then(() => _bumpOrigin((x) => x + 1)); }, []);

      const tax = (detail && detail.taxonomy) || {};
      const subs = (detail && detail.subquestions) || [];
      const findings = (detail && detail.label_findings) || [];
      const fmtFindings = (detail && detail.format_findings) || [];
      // 一个单元只做一次知识点标签比对，因此只呈现一条「待人工修改」。
      // 旧协议记录会按每个小题、每个字段各拆一条（十几条其实只是同一次比对），
      // single=true 时整单元合并为一条；single=false 时格式疑点按类型归并。
      // 两种模式都保留全部 finding_id，一次决定覆盖整组，绝不丢证据、不伪造通过。
      function consolidateFindings(list, single) {
        const out = [];
        const byKey = new Map();
        for (const f of (list || [])) {
          const raw = String(f.question || f.message || f.reason || f.field || '').trim();
          const head = raw.split(/[：:]/)[0].replace(/^第\s*\d+\s*题/, '').trim();
          const field = String(f.field || '').trim().toLowerCase();
          const key = single ? 'single' : (field || 'other') + '|' + (head || raw);
          let g = byKey.get(key);
          if (!g) {
            g = { key, ids: [], items: [], numbers: [], fields: [],
                  head: single ? '标签比对差异' : (head || raw || f.field || '待人工确认') };
            byKey.set(key, g); out.push(g);
          }
          if (f.finding_id) g.ids.push(f.finding_id);
          g.items.push(f);
          if (field && g.fields.indexOf(field) < 0) g.fields.push(field);
          const m = raw.match(/第\s*(\d+)\s*题/);
          if (m && g.numbers.indexOf(m[1]) < 0) g.numbers.push(m[1]);
        }
        for (const g of out) g.numbers.sort((a, b) => (Number(a) || 0) - (Number(b) || 0));
        return out;
      }
      const groupedFindings = consolidateFindings(findings, true);
      const groupedFmt = consolidateFindings(fmtFindings, false);

      function setL(key, val) { setLabels((x) => ({ ...(x || {}), [key]: val })); }
      async function saveLabels() {
        setBusy('save');
        setLabelError('');
        setLabelNotice('');
        try {
          await jpost('/batches/' + encodeURIComponent(batchName) + '/units/' + encodeURIComponent(unitId), { labels });
          setLabelDlg(false);
          await load({ force: true, silent: true });
          setLabelNotice('标签修改已保存');
        } catch (e) { setLabelError(String(e.message || e)); }
        finally { setBusy(''); }
      }

      React.useEffect(() => {
        let cancelled = false;
        setAiBusy('');
        setAiNotice('');
        setAiModal(null);
        setAiModalOpen(false);
        jget('/batches/' + encodeURIComponent(batchName) + '/ai-supplement').then((st) => {
          if (cancelled || !st || st.status !== 'running') return;
          setAiBusy(String(st.mode || 'running'));
          setAiModal(st);
        }).catch(() => {});
        return () => { cancelled = true; };
      }, [batchName]);

      React.useEffect(() => {
        if (!aiModal || aiModal.status !== 'running') return undefined;
        let cancelled = false;
        const poll = async () => {
          try {
            const st = await jget('/batches/' + encodeURIComponent(batchName) + '/ai-supplement');
            if (cancelled) return;
            setAiModal(st);
            if (st.status === 'done') {
              setAiBusy('');
              setAiNotice(String(st.message || (st.applied ? `✓ 已生成 ${st.applied} 项（已标注 AI 生成）` : '无可补充缺项')));
              invalidateBatchUnits(batchName);
              loadBatchUnitsBundle(batchName, { force: true }).catch(() => {});
              load({ force: true, silent: true });
            } else if (st.status === 'error') {
              setAiBusy('');
              setAiNotice('补充失败：' + String(st.error || st.message || '未知错误'));
            } else if (st.status !== 'running') {
              setAiBusy('');
            }
          } catch (e) {
            if (!cancelled) {
              setAiBusy('');
              setAiModal((prev) => prev ? { ...prev, status: 'error', error: String(e.message || e) } : null);
            }
          }
        };
        const timer = setInterval(poll, 900);
        return () => { cancelled = true; clearInterval(timer); };
      }, [aiModal?.status, batchName, load]);

      async function openPaperPreview() {
        setPaperDlg(true);
        setPaperLoading(true);
        setPaperError('');
        try {
          const res = await jget('/batches/' + encodeURIComponent(batchName) + '/paper-preview');
          setPaperData(res);
        } catch (e) {
          setPaperError(String(e.message || e));
        } finally {
          setPaperLoading(false);
        }
      }

      async function saveContentEdit(patch) {
        if (busy) return;
        setBusy('contentEdit');
        setContentEditError('');
        try {
          await jpost('/batches/' + encodeURIComponent(batchName) + '/content-edit', patch);
          setContentEditDlg(false);
          setLabelNotice('已保存文章大意/答案解析');
          await load({ force: true, silent: true });
        } catch (e) {
          setContentEditError(String(e.message || e));
        } finally {
          setBusy('');
        }
      }

      async function runAiSupplement(mode) {
        if (aiBusy) {
          if (aiModal) setAiModalOpen(true);
          return;
        }
        setAiBusy(mode);
        setAiNotice('');
        setAiModal({ status: 'running', mode, total: 0, completed: 0, applied: 0, message: '正在扫描并启动生成…', error: '' });
        setAiModalOpen(true);
        try {
          const res = await jpost('/batches/' + encodeURIComponent(batchName) + '/ai-supplement', { mode });
          setAiModal(res);
          if (res.status === 'done') {
            setAiBusy('');
            setAiNotice(String(res.message || (res.applied ? `✓ 已生成 ${res.applied} 项（已标注 AI 生成）` : '无可补充缺项')));
            invalidateBatchUnits(batchName);
            loadBatchUnitsBundle(batchName, { force: true }).catch(() => {});
            await load({ force: true, silent: true });
          } else if (res.status === 'error') {
            setAiBusy('');
            setAiNotice('补充失败：' + String(res.error || res.message || '未知错误'));
          }
        } catch (e) {
          setAiBusy('');
          setAiModal({ status: 'error', mode, total: 0, completed: 0, applied: 0, message: '生成失败', error: String(e.message || e) });
          setAiNotice('补充失败：' + String(e.message || e));
        }
      }

      // ---- 内容编辑：题干/选项/答案（逐小题）+ 正文/解析（整组共用）----
      // 选项 [{label,text}] ↔ 多行文本，编辑时不显示 [object Object]，保存时保留字母标签。
      function optionsToText(options) {
        if (!Array.isArray(options)) return typeof options === 'string' ? options : '';
        return options.map((o) => {
          if (o && typeof o === 'object') {
            const label = String(o.label || '').replace(/[.．、]\s*$/, '');
            const text = o.text != null ? o.text : (o.content != null ? o.content : '');
            return label ? (label + '. ' + text) : String(text);
          }
          return String(o);
        }).join('\n');
      }
      function textToOptions(text, original) {
        const lines = String(text || '').split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
        return lines.map((line, index) => {
          const m = line.match(/^([A-Ha-h])\s*[.．、:：]\s*(.*)$/);
          if (m) return { label: m[1].toUpperCase(), text: m[2] };
          const old = Array.isArray(original) ? original[index] : null;
          const label = old && typeof old === 'object' && old.label ? old.label : String.fromCharCode(65 + index);
          return { label, text: line };
        });
      }
      // 保留加载时显示的字符串，保存时只比较实际改动，不重写未触碰的字段（原卷弹窗仅维护材料与小题）。
      function makeContentSnapshot(d) {
        const item = (d && d.item) || {};
        return {
          material: item.material != null ? String(item.material) : '',
          subs: ((d && d.subquestions) || []).map((s) => ({
            number: s.number,
            display_number: s.display_number,
            stem: s.stem != null ? String(s.stem) : '',
            optionsText: optionsToText(s.options),
            options: s.options,
            answer: s.answer != null ? String(s.answer) : '',
          })),
        };
      }
      function initContent(d) {
        const snapshot = makeContentSnapshot(d);
        setContent(snapshot);
        setContentOriginal(snapshot);
      }
      function buildContentPatch(edited, original) {
        if (!edited || !original) return null;
        const unit = {};
        const expected = {};
        for (const key of ['material']) {
          if (edited[key] !== original[key]) {
            unit[key] = edited[key];
            expected[key] = original[key];
          }
        }
        const items = [];
        edited.subs.forEach((sub, i) => {
          const before = original.subs[i];
          const item = { number: sub.number };
          if (sub.stem !== before.stem) item.stem = sub.stem;
          if (sub.optionsText !== before.optionsText) item.options = textToOptions(sub.optionsText, before.options);
          if (sub.answer !== before.answer) item.answer = sub.answer;
          if (Object.keys(item).length > 1) items.push(item);
        });
        const patch = {};
        if (Object.keys(unit).length) Object.assign(patch, { unit, expected });
        if (items.length) patch.items = items;
        return Object.keys(patch).length ? patch : null;
      }
      function setSubField(idx, key, val) {
        if (contentSave.current) return;
        setContent((c) => {
          const subs = c.subs.slice();
          subs[idx] = { ...subs[idx], [key]: val };
          return { ...c, subs };
        });
      }
      function setUnitField(key, val) {
        if (contentSave.current) return;
        setContent((c) => ({ ...c, [key]: val }));
      }
      function openContent() {
        // 已发出的后台刷新也不能替换本次编辑的基线。
        requestGate.current.cancel();
        setContentError('');
        setContentDlg(true);
      }
      function closeContent() {
        if (contentSave.current) return;
        setContent(contentOriginal);
        setContentError('');
        setContentDlg(false);
        load({ force: true, silent: true });
      }
      async function saveContent() {
        if (!contentPatch || contentSave.current) return;
        const request = {};
        contentSave.current = request;
        setBusy('content');
        setContentError('');
        setLabelNotice('');
        try {
          const saved = await jpost('/batches/' + encodeURIComponent(batchName) + '/units/' + encodeURIComponent(unitId), contentPatch);
          if (contentSave.current !== request) return;
          const formal = saved.applied?.applied;
          const draft = contentPatch.items || Object.hasOwn(contentPatch.unit || {}, 'material');
          if (formal) {
            setLabelNotice('正式内容已保存；体检、审核与上传需要重新处理'
              + (draft ? '。材料、题干、选项或答案的改动仅保存为草稿' : ''));
          } else if (draft) {
            setLabelNotice('改动仅保存为草稿，未写入正式批次');
          } else {
            setLabelNotice('正式内容未变化');
          }
          setContentDlg(false);
          load({ force: true, silent: true });
        } catch (e) {
          if (contentSave.current === request) setContentError(String(e.message || e));
        } finally {
          if (contentSave.current === request) {
            contentSave.current = null;
            setBusy('');
          }
        }
      }
      async function decide(f, status) {
        // 合并后的卡片携带整组 finding_id：逐条落库，仍由后端各自校验与记决定日志。
        const ids = (Array.isArray(f.ids) && f.ids.length) ? f.ids : [f.finding_id];
        try {
          for (const id of ids) {
            if (!id) continue;
            await jpost('/batches/' + encodeURIComponent(batchName) + '/decisions', {
              item: { finding_id: id, status, note: f.resolution_note || '' },
            });
          }
          load({ force: true, silent: true });
        } catch (e) { setErr(String(e.message || e)); }
      }

      // ---- 单元列表：过滤 + 搜索 ----
      const allRows = rows || [];
      const filteredRows = React.useMemo(() => {
        return allRows.filter((r) => {
          if (filter === 'open' && !r.needs_human) return false;
          if (q) {
            const hay = (String(r.label || '') + String(r.number || '') + String(r.type || '') + String((r.knowledge || []).join(' '))).toLowerCase();
            if (!hay.includes(q.toLowerCase())) return false;
          }
          return true;
        });
      }, [allRows, filter, q]);

      // 题组列表滚动跟随当前题：↑/↓ 切题时把选中行滚进可视区（只滚最小距离），
      // 不再整体回到顶部。仅作用于左侧列表容器，不影响右侧预览/审核区。
      React.useEffect(() => {
        const box = listScrollRef.current;
        if (!box) return;
        let row = null;
        for (const el of box.querySelectorAll('[data-unit]')) {
          if (el.getAttribute('data-unit') === String(unitId)) { row = el; break; }
        }
        if (!row) return;
        const br = row.getBoundingClientRect();
        const bc = box.getBoundingClientRect();
        if (br.top < bc.top + 4) box.scrollTop -= (bc.top - br.top) + 8;
        else if (br.bottom > bc.bottom - 4) box.scrollTop += (br.bottom - bc.bottom) + 8;
      }, [unitId, filteredRows]);

      // 兜底确保当前题的图片（含表格单元格配图，排除音频）已进入缓存。
      React.useEffect(() => {
        if (!detail) return;
        prefetchImages(collectBlockImageUrls(detail.preview_blocks), 4);
      }, [detail, unitId]);

      // ---- 键盘 ↑/↓（或 j/k）切换题组；输入框内不拦截 ----
      React.useEffect(() => {
        function onKey(e) {
          if (e.metaKey || e.ctrlKey || e.altKey) return;
          const tag = (e.target && e.target.tagName || '').toLowerCase();
          if (tag === 'input' || tag === 'textarea' || tag === 'select' || (e.target && e.target.isContentEditable)) return;
          if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'j' && e.key !== 'k') return;
          const list = filteredRows;
          if (!list.length) return;
          let idx = list.findIndex((r) => (r.unit_id || r.number) === unitId);
          if (idx < 0) idx = 0;
          const delta = (e.key === 'ArrowDown' || e.key === 'j') ? 1 : -1;
          const next = list[Math.min(list.length - 1, Math.max(0, idx + delta))];
          if (next && (next.unit_id || next.number) !== unitId) {
            e.preventDefault();
            onSelectUnit(next.unit_id || next.number);
          } else if (next) {
            e.preventDefault();
          }
        }
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
      }, [filteredRows, unitId, onSelectUnit]);

      const sel = (v) => ({ value: v || '', onChange: (e) => { /* handled by caller */ } });

      // 原文预览块 -> React
      // 选项标签：形如 "A." / "\tB." / "（C）" 的短文本。
      function optionLabel(text) {
        const t = String(text == null ? '' : text).replace(/<[^>]*>/g, '').replace(/\*/g, '').replace(/[\s\u3000]+/g, '');
        return /^[A-Ha-h][.．、)）]?$/.test(t) || /^[（(][A-Ha-h][)）]$/.test(t) ? t : null;
      }
      function optionLetter(label) {
        const match = String(label || '').match(/[A-H]/i);
        return match ? match[0].toUpperCase() : '';
      }
      function renderBlocks(blocks, bulkBold) {
        const list = blocks || [];
        const out = [];
        const isOptLabel = (b) => b && b.type === 'text' && optionLabel(b.text) !== null;
        const isImg = (b) => b && b.type === 'image';
        // 选项组识别：以选项标签 A/B/C/D 为锚，图跟着标签走（letter 由标签决定）。
        // 先聚出「连续选项段」：标签之间可夹图；再判断该段是图在前(`[IMG]A.`)还是
        // 图在后(`A.[IMG]`)，统一配对，避免夹在选项间的图被错判。
        const labelToImg = new Map();   // 块索引(标签) → 图块
        const consumedImg = new Set();  // 已被选项消费的图块索引
        // 判定每个选项标签配哪张图：先看紧邻前块是否为「尚未被消费」的图（图在前形态
        // `[IMG]A.`），是则归本标签；否则看紧邻后块是否为图（图在后形态 `A.[IMG]`）。
        // 按块顺序遍历，前图先被左侧标签消费，右侧重复的图（本属下一选项）自然跳过。
        for (let k = 0; k < list.length; k++) {
          if (!isOptLabel(list[k])) continue;
          if (isImg(list[k - 1]) && !consumedImg.has(k - 1)) {
            labelToImg.set(k, list[k - 1]); consumedImg.add(k - 1);
          } else if (isImg(list[k + 1]) && !consumedImg.has(k + 1)) {
            labelToImg.set(k, list[k + 1]); consumedImg.add(k + 1);
          }
        }
        for (let i = 0; i < list.length; i++) {
          const b = list[i];
          if (consumedImg.has(i)) continue;  // 该图已作为选项图渲染
          // 以连续的「有图的选项标签」组建成一排。
          if (isOptLabel(b) && labelToImg.has(i)) {
            const cells = [];
            let j = i;
            while (j < list.length && isOptLabel(list[j]) && labelToImg.has(j)) {
              const lab = optionLabel(list[j].text);
              cells.push({ label: lab, letter: optionLetter(lab), block: labelToImg.get(j) });
              j++;
              // 跳过夹在选项之间的「已作为某选项图消费」的图块，直到下一个选项标签。
              while (j < list.length && consumedImg.has(j) && isImg(list[j])) j++;
            }
            if (cells.length >= 1) {
              out.push(h('div', { key: 'optrow:' + i, className: 'wf-opt-row' },
                cells.map((c, ci) => {
                  const optionIndex = Math.max(0, (c.letter || 'A').charCodeAt(0) - 65);
                  return h('div', {
                    key: (c.letter || ci) + ':' + ci,
                    className: 'wf-opt-cell',
                    style: { gridColumn: String((optionIndex % 4) + 1), gridRow: String(Math.floor(optionIndex / 4) + 1) },
                  },
                    h('span', { className: 'wf-opt-label' }, c.label),
                    h('div', { className: 'wf-opt-frame' },
                      h(PreviewImage, { url: c.block.url, name: c.block.name })));
                })));
              i = j - 1;
              continue;
            }
          }
          if (b.type === 'text') {
            const display = paperDisplayText(b.text, bulkBold);
            if (bulkBold && !display.trim() && String(b.text || '').trim()) continue;
            const heading = isHeading(display);
            out.push(h('div', {
              key: i,
              className: 'wf-line' + (heading ? ' wf-heading' : ''),
              dangerouslySetInnerHTML: { __html: markLine(display) },
            }));
            continue;
          }
          if (b.type === 'image') {
            out.push(h('div', { key: 'body-image:' + String(b.url || b.name || '') + ':' + i, className: 'wf-body-image-frame' },
              h(PreviewImage, { url: b.url, name: b.name })));
            continue;
          }
          if (b.type === 'table') {
            // 有 Word 合并跨度时按真实 rowspan/colspan 渲染；没有就退回平面网格。
            const structured = Array.isArray(b.structured) ? b.structured : null;
            const cellImages = b.cellImages || [];
            const cellSpans = b.cellSpans || [];
            const rows = [];
            if (structured) {
              structured.forEach((row) => {
                const cells = (row.cells || []).map((cell) => ({
                  text: String(cell.text || ''),
                  images: cell.images || [],
                  rowspan: Number(cell.rowspan) || 1,
                  colspan: Number(cell.colspan) || 1,
                  header: !!cell.header,
                }));
                // 纯平面行沿用原来的短文本表头判定；有合并跨度的行不靠猜。
                const plain = cells.every((cell) => cell.rowspan === 1 && cell.colspan === 1);
                if (plain && isHeaderRow(cells.map((cell) => cell.text))) {
                  cells.forEach((cell) => { cell.header = true; });
                }
                rows.push(cells);
              });
            } else {
              (b.rows || []).forEach((row, ri) => {
                const head = isHeaderRow(row);
                const rowCells = cellImages[ri] || [];
                rows.push((row || []).map((cell, ci) => ({
                  text: cell == null ? '' : String(cell),
                  images: (rowCells[ci] || []).filter((im) => im && im.url),
                  rowspan: Number.isInteger((cellSpans[ri] || [])[ci]?.rowspan) ? cellSpans[ri][ci].rowspan : 1,
                  colspan: Number.isInteger((cellSpans[ri] || [])[ci]?.colspan) ? cellSpans[ri][ci].colspan : 1,
                  header: head,
                })));
              });
            }
            out.push(h('div', { key: i, style: { margin: '8px 0', maxWidth: '100%', minWidth: 0 } },
              h('table', { className: 'wf-table' },
                h('tbody', null, rows.map((row, ri) => h('tr', { key: ri }, row.map((cell, ci) => {
                  // 单元格内的图片与该单元格文字一起显示，图片在前、文字在后。
                  const imgs = (cell.images || []).filter((im) => im && im.url);
                  const children = [];
                  imgs.forEach((im, k) => children.push(
                    h('span', { key: 'img' + k, className: 'wf-table-thumb' },
                      h(PreviewImage, { url: im.url, name: im.name }))));
                  if (cell.text !== '') children.push(
                    h('span', { key: 'txt', dangerouslySetInnerHTML: { __html: markLine(cell.text, bulkBold) } }));
                  const props = { key: ci, className: imgs.length ? 'wf-table-imgcell' : undefined };
                  if (cell.rowspan > 1) props.rowSpan = cell.rowspan;
                  if (cell.colspan > 1) props.colSpan = cell.colspan;
                  return cell.header ? h('th', props, children) : h('td', props, children);
                })))))));
            continue;
          }
        }
        return out;
      }

      // 预览小字标签：灰色小标 + 值
      function plItem(k, v) {
        if (v === null || v === undefined || v === '') return null;
        return h('span', { className: 'wf-preitem' },
          h('span', { style: { color: 'var(--dsw-alias-label-secondary)', opacity: .8, marginRight: 3 } }, k),
          h('span', { style: { color: 'var(--dsw-alias-label-primary)' } }, String(v)));
      }

      // ---- 左栏标签审核面板：知识点对比 + 难度/来源/分类对比 ----
      // 取某一轮（一审/二审）的单元级 assessment；未执行审核时返回 null。
      function roundAssessment(comparison, roundId) {
        const rounds = (comparison && comparison.rounds) || [];
        const r = rounds.find((x) => x.round_id === roundId);
        if (!r) return null;
        const checks = (r.checks || []).filter((c) => c && c.assessment && Object.keys(c.assessment).length);
        // 二审按小题记录，不能过滤带 number 的 check，也不能把一条抽样结论当作整组结论。
        const unit = checks.find((c) => c.number == null);
        return { status: r.status, assessment: unit ? unit.assessment : null, checks, unit };
      }
      // 比较与后端 review_consensus._normal 同源：完整路径集合，忽略顺序和重复。
      function labelSetKey(value) {
        return JSON.stringify(Array.isArray(value) ? [...new Set(value)].sort() : value);
      }
      // 把知识点数组取末级短名，方便对照。
      function kpShort(list) {
        if (!Array.isArray(list)) return String(list == null ? '' : list);
        return list.map((x) => {
          const s = String(x == null ? '' : x).trim();
          const parts = s.split(/[\/>＞]/).map((p) => p.trim()).filter(Boolean);
          return parts.length > 1 ? parts[parts.length - 1] : s;
        }).filter(Boolean);
      }
      function answerPointDiff(check) {
        const expected = check?.expected_answer_points;
        const reviewed = check?.assessment?.answer_points;
        return Array.isArray(reviewed) && Array.isArray(expected) && expected.length > 0
          && labelSetKey(expected) !== labelSetKey(reviewed);
      }
      function reviewedLabels(check) {
        const assessment = check?.assessment;
        if (!assessment) return null;
        return [...new Set([...(assessment.knowledge || []),
          ...(assessment.answer_points || []).map((name) => '词汇/答案考点/' + name)])];
      }
      // 一行：板块标题 + 「值」；仅把存在差异的标签标红，一致的标签保持正常颜色。
      // 知识点只展示末级短名（用户要求简洁），完整路径放在 title 里备查。
      function cmpRow(label, value, opts) {
        const o = opts || {};
        const empty = value == null || value === '' || (Array.isArray(value) && !value.length);
        const hasItemDiff = !empty && Array.isArray(value) && o.diffPaths instanceof Set;
        const text = empty ? (o.pending ? '待审核' : (o.mismatch ? '（漏标）' : '—')) : (Array.isArray(value) ? value.join('、') : String(value));
        const color = (!hasItemDiff && o.mismatch)
          ? 'var(--dsw-alias-state-error-primary)'
          : (empty ? 'var(--dsw-alias-label-secondary)' : 'var(--dsw-alias-label-primary)');
        const title = Array.isArray(o.titles) && o.titles.length ? o.titles.join('、') : undefined;
        let valueNode;
        if (hasItemDiff) {
          const parts = [];
          value.forEach((item, idx) => {
            const fullPath = Array.isArray(o.titles) && o.titles[idx] != null ? o.titles[idx] : item;
            const isDiff = o.diffPaths.has(fullPath);
            if (idx > 0) parts.push('、');
            parts.push(h('span', {
              key: String(fullPath) + ':' + idx,
              title: fullPath,
              style: isDiff
                ? { color: 'var(--dsw-alias-state-error-primary)', fontWeight: 600 }
                : { color: 'var(--dsw-alias-label-primary)', fontWeight: 400 },
            }, item));
          });
          valueNode = h('span', { style: { flex: 1, minWidth: 0, fontSize: 12.5, color: 'var(--dsw-alias-label-primary)', wordBreak: 'break-word' } }, ...parts);
        } else {
          valueNode = h('span', { title, style: { flex: 1, minWidth: 0, fontSize: 12.5, color, fontWeight: o.mismatch ? 600 : 400, wordBreak: 'break-word' } }, text);
        }
        const children = [
          h('span', { style: { flex: '0 0 auto', width: 64, fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } }, label),
          valueNode,
        ];
        if (o.modified) children.push(h('span', { style: { flex: '0 0 auto', fontSize: 11, color: 'var(--dsw-alias-state-warn-primary)' } }, '已修改'));
        return h('div', { style: { display: 'flex', gap: 8, alignItems: 'baseline', padding: '3px 0' } }, ...children);
      }
      function labelAuditPanels(detail) {
        const comp = detail.comparison || {};
        const orig = (comp.original && comp.original.labels) || {};
        const cur = detail.labels || {};
        const r1 = roundAssessment(comp, 'labels');   // 一审
        const kpSame = (a, b) => labelSetKey(a) === labelSetKey(b);
        const cleanPaths = (list) => Array.isArray(list) ? list.filter((x) => String(x == null ? '' : x).trim()) : list;

        // 比较保留完整路径；同名叶子也必须能看出所属目录分支。
        const hasOriginalKnowledge = Array.isArray(orig.knowledge);
        const kpOrig = hasOriginalKnowledge ? orig.knowledge : (cur.knowledge || []);
        const kp1 = r1 && r1.assessment ? r1.assessment.knowledge : null;
        const mergedInfo = comp.merged && typeof comp.merged === 'object' ? comp.merged : null;
        const autoMerged = mergedInfo && Array.isArray(mergedInfo.auto_merged)
          ? mergedInfo.auto_merged
          : [...new Set([...kpOrig, ...(Array.isArray(kp1) ? kp1 : [])])];
        const kpModified = !!(cur.knowledge_manually_saved || (mergedInfo && mergedInfo.manually_edited));
        const answerPaths = [
          ...(cur.knowledge || []).filter((path) => path.startsWith('词汇/答案考点/')),
          ...(cur.answer_point_tags || []).map((name) => '词汇/答案考点/' + name),
        ];
        const displayedOrigPaths = cleanPaths([...new Set([...kpOrig, ...answerPaths])]);
        const origPathSet = new Set([...kpOrig, ...answerPaths]);
        function reviewedDiffSet(check, reviewedPaths) {
          if (!Array.isArray(reviewedPaths) || !reviewedPaths.length) return null;
          const hasExpectedAp = Array.isArray(check?.expected_answer_points) && check.expected_answer_points.length > 0;
          const expectedApSet = hasExpectedAp
            ? new Set(check.expected_answer_points.map((name) => '词汇/答案考点/' + name))
            : null;
          const diff = new Set();
          reviewedPaths.forEach((path) => {
            const isAp = typeof path === 'string' && path.startsWith('词汇/答案考点/');
            const matched = isAp && expectedApSet ? expectedApSet.has(path) : origPathSet.has(path);
            if (!matched) diff.add(path);
          });
          return diff;
        }
        const r1Paths = cleanPaths(reviewedLabels(r1?.unit));
        const r1Mismatch = !!(r1?.assessment && !kpSame(kp1, kpOrig)) || answerPointDiff(r1?.unit);
        const r1Diff = r1Mismatch ? reviewedDiffSet(r1?.unit, r1Paths) : null;
        // 原标签里若存在一审判定未包含的标签，也只突出显示该差异标签。
        const origDiffPaths = new Set();
        if (r1Mismatch && Array.isArray(r1Paths)) {
          const r1Set = new Set(r1Paths);
          displayedOrigPaths.forEach((path) => {
            if (!r1Set.has(path)) origDiffPaths.add(path);
          });
        }
        // 标签合并：合并原标签与一审知识点（去重并集）；人工在「修改标签」保存过则以人工结果为准。
        const hasMerged = kpModified || !!(r1 && r1.assessment) || !!(mergedInfo && mergedInfo.has_round1);
        const mergedBase = kpModified
          ? (Array.isArray(cur.knowledge) ? cur.knowledge : (mergedInfo && Array.isArray(mergedInfo.knowledge) ? mergedInfo.knowledge : autoMerged))
          : (mergedInfo && Array.isArray(mergedInfo.knowledge) ? mergedInfo.knowledge : autoMerged);
        const mergedPaths = hasMerged ? cleanPaths([...new Set([...mergedBase, ...answerPaths])]) : null;
        const kpRows = h('div', { style: { padding: '2px 0 8px' } },
          h('div', { style: { fontSize: 12.5, fontWeight: 600, margin: '2px 0 6px' } }, '知识点标签'),
          cmpRow('原标签', kpShort(displayedOrigPaths), { titles: displayedOrigPaths, diffPaths: origDiffPaths.size ? origDiffPaths : undefined }),
          cmpRow('一审标签', kpShort(r1Paths), { pending: !r1 || !r1.assessment, mismatch: r1Mismatch, diffPaths: r1Mismatch && (r1Diff?.size || origDiffPaths.size) ? r1Diff : undefined, titles: r1Paths }),
          cmpRow('标签合并', kpShort(mergedPaths), { pending: !hasMerged, modified: kpModified, titles: mergedPaths }),
        );

        // 题型 / 子类：始终展示到最末级（叶子子类，如 单词拼写 / 句意 vs 单词拼写 / 汉语提示）；
        // 上级题型相同、末级子类不同时，仅把有差异的末级叶子标红加粗。
        const panels = [h('div', { key: 'kp' }, kpRows)];
        const r1Assess = r1 && r1.assessment ? r1.assessment : null;
        const baseType = String(orig.type || cur.type || '').trim();
        const r1Type = r1Assess ? String(r1Assess.type || '').trim() : '';
        const origSubs = cleanPaths(Array.isArray(orig.subtypes) ? orig.subtypes : (Array.isArray(cur.subtypes) ? cur.subtypes : [])) || [];
        const r1Subs = r1Assess && Array.isArray(r1Assess.subtypes) ? cleanPaths(r1Assess.subtypes) : null;
        const hasTypeInfo = !!(baseType || r1Type || origSubs.length || (Array.isArray(r1Subs) && r1Subs.length));
        if (hasTypeInfo) {
          const typeMismatch = !!(baseType && r1Type && baseType !== r1Type);
          const subMismatch = !!(Array.isArray(r1Subs) && labelSetKey(r1Subs) !== labelSetKey(origSubs));
          const origSubSet = new Set(origSubs);
          const r1SubSet = new Set(r1Subs || []);
          function typeLeafRow(rowLabel, mainType, subs, otherSubSet, opts) {
            const o = opts || {};
            const empty = !mainType && (!Array.isArray(subs) || !subs.length);
            let valueNode;
            if (empty) {
              valueNode = h('span', {
                style: { flex: 1, minWidth: 0, fontSize: 12.5, color: 'var(--dsw-alias-label-secondary)', wordBreak: 'break-word' },
              }, o.pending ? '待审核' : '—');
            } else {
              const parts = [];
              if (mainType) {
                parts.push(h('span', {
                  key: 't',
                  title: '题型：' + mainType,
                  style: o.typeDiff
                    ? { color: 'var(--dsw-alias-state-error-primary)', fontWeight: 600 }
                    : { color: 'var(--dsw-alias-label-primary)', fontWeight: 400 },
                }, mainType));
              }
              const leafList = Array.isArray(subs) && subs.length
                ? subs
                : (o.subDiff ? ['（无子类）'] : []);
              if (leafList.length) {
                if (mainType) {
                  parts.push(h('span', { key: 'sep', style: { color: 'var(--dsw-alias-label-secondary)', margin: '0 4px' } }, '/'));
                }
                leafList.forEach((leaf, idx) => {
                  const isDiff = o.subDiff && (leaf === '（无子类）' || !otherSubSet.has(leaf));
                  if (idx > 0) parts.push('、');
                  parts.push(h('span', {
                    key: 's:' + leaf + ':' + idx,
                    title: '子类：' + leaf,
                    style: isDiff
                      ? { color: 'var(--dsw-alias-state-error-primary)', fontWeight: 600 }
                      : { color: 'var(--dsw-alias-label-primary)', fontWeight: 400 },
                  }, leaf));
                });
              }
              valueNode = h('span', { style: { flex: 1, minWidth: 0, fontSize: 12.5, color: 'var(--dsw-alias-label-primary)', wordBreak: 'break-word' } }, ...parts);
            }
            return h('div', { style: { display: 'flex', gap: 8, alignItems: 'baseline', padding: '3px 0' } },
              h('span', { style: { flex: '0 0 auto', width: 64, fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } }, rowLabel),
              valueNode,
            );
          }
          const typeRows = h('div', { style: { padding: '6px 0 4px', borderTop: '1px dashed var(--dsw-alias-border-l1)' } },
            h('div', { style: { fontSize: 12.5, fontWeight: 600, margin: '2px 0 6px' } }, '题型 / 子类'),
            typeLeafRow('原标签', baseType, origSubs, r1SubSet, { typeDiff: typeMismatch, subDiff: subMismatch }),
            typeLeafRow('一审标签', r1Type, r1Subs || [], origSubSet, { pending: !r1Assess, typeDiff: typeMismatch, subDiff: subMismatch }),
          );
          panels.push(h('div', { key: 'type' }, typeRows));
        }
        return panels;
      }

      // 审核说明：越短越好，只一句话说清差异。审核范畴只有「知识点标签」，
      // 难度/来源/分类等基本标签不参与审核，也不在这里罗列。
      function auditNotes(detail) {
        const comp = detail.comparison || {};
        const orig = (comp.original && comp.original.labels) || {};
        const cur = detail.labels || {};
        const r1 = roundAssessment(comp, 'labels');
        const baseType = String(orig.type || cur.type || '').trim();
        const joinList = (v) => (Array.isArray(v) ? v.filter(Boolean).join('、') : (v == null ? '' : String(v)));
        const baseSub = joinList(orig.subtypes || cur.subtypes);
        const mainDiffs = [];
        const subDiffs = [];
        const checkType = (name, a) => {
          if (!a) return;
          const t = String(a.type || '').trim();
          if (t && baseType && t !== baseType) mainDiffs.push(name + '「' + t + '」');
          const st = joinList(a.subtypes);
          if (Array.isArray(a.subtypes) && labelSetKey(a.subtypes) !== labelSetKey(orig.subtypes || cur.subtypes)) subDiffs.push(name + '「' + (st || '无') + '」');
        };
        checkType('一审', r1 && r1.assessment);
        const notes = [];
        if (mainDiffs.length) notes.push('题型差异：' + mainDiffs.join('、') + '（原「' + (baseType || '无') + '」）');
        if (subDiffs.length) notes.push('子类差异：' + subDiffs.join('、') + '（原「' + (baseSub || '无') + '」）');
        if (notes.length) return notes;
        // 有知识点差异时，用一句简短原因说明为什么会不一致（有就写，没有就空）。
        const kpDiff = (typeof hasKnowledgeDiff === 'function') ? hasKnowledgeDiff(detail) : false;
        if (kpDiff) {
          const reason = ((r1 && r1.checks) || []).map((c) => String(c.rationale || '').trim()).find(Boolean) || '';
          const short = reason ? reason.slice(0, 60) : '一审与原标签判断不同';
          return ['差异原因：' + short];
        }
        return [];
      }
      // 是否有一审与原标签不一致的知识点（仅用于审核说明的空态文案）。
      function hasKnowledgeDiff(detail) {
        const comp = detail.comparison || {};
        const orig = (comp.original && comp.original.labels) || {};
        const cur = detail.labels || {};
        const same = (a, b) => labelSetKey(a) === labelSetKey(b);
        const base = orig.knowledge || cur.knowledge || [];
        const r1 = roundAssessment(comp, 'labels');
        if (r1?.assessment && (!same(r1.assessment.knowledge, base) || answerPointDiff(r1.unit))) return true;
        return false;
      }

      // 两轮审核（一审全量 / 二审抽 20%）统一、紧凑的展示。
      // 原则：只显示一次；正常即"全通过"；有问题的在对应审核节点上点名；
      // 只讲差异，不堆完整层级，标签保持短小。
      const ROUND_SHORT = { labels: '一审' };
      function roundName(r) { return ROUND_SHORT[r.round_id] || r.name || r.round_id || '审核'; }
      // 值裁剪：知识点等长串只保留末级，避免把整条层级拉得很长。
      function shortVal(v) {
        const one = (x) => {
          const s = String(x == null ? '' : x).trim();
          if (!s) return '—';
          const parts = s.split(/[\/>＞]/).map((p) => p.trim()).filter(Boolean);
          return parts.length > 1 ? parts[parts.length - 1] : s;
        };
        if (Array.isArray(v)) return v.length ? v.map(one).join('、') : '—';
        return one(v);
      }
      // 一个审核节点是否"通过"：有判定且 verdict=ok，且无差异。
      // 返回 {kind, note}：kind ∈ ok/diff/uncertain/na/pending；note 为需要在该节点上点名的短说明。
      function nodeState(r) {
        const chs = r.checks || [];
        if (!chs.length) {
          const st = String(r.status || '').trim();
          if (st === '未涉及' || st === '未覆盖') return { kind: 'na' };
          if (st === '已提供' || st === '已覆盖') return { kind: 'ok' };
          // 历史来源未验证 / 待重新审核 / 未提供 —— 都是该节点尚未完成的真实状态，原样点名。
          return { kind: 'pending', note: st || '待审核' };
        }
        const diff = chs.find((c) => c.verdict === 'error' || (c.differences || []).length);
        if (diff) {
          const d = (diff.differences || [])[0];
          return { kind: 'diff', note: d ? ('原 ' + shortVal(d.original) + ' → ' + shortVal(d.reviewed)) : '' };
        }
        const unc = chs.find((c) => c.verdict === 'uncertain');
        if (unc) return { kind: 'uncertain', note: unc.rationale ? String(unc.rationale).slice(0, 40) : '' };
        if (chs.every((c) => c.verdict === 'ok')) return { kind: 'ok' };
        return { kind: 'pending', note: String(r.status || '待审核') };
      }
      function stateChip(kind) {
        const map = {
          ok: ['✓', 'var(--dsw-alias-label-primary)', '通过'],
          diff: ['差异', 'var(--dsw-alias-state-warn-primary)', '有差异'],
          uncertain: ['疑点', 'var(--dsw-alias-state-warn-primary)', '有疑点'],
          na: ['—', 'var(--dsw-alias-label-secondary)', '未涉及'],
          pending: ['待审', 'var(--dsw-alias-label-secondary)', '待审核'],
        };
        const [txt, color, title] = map[kind] || map.pending;
        return h('span', { title, style: { color, fontWeight: 600, flexShrink: 0 } }, txt);
      }
      // 单个审核节点一行：一审/二审 + 状态；未完成时直接显示其真实状态文字
      // （如"历史来源未验证""待重新审核"），不再叠一个笼统的"待审"造成重复。
      function roundNodeRow(r, st) {
        const label = h('span', { style: { fontWeight: 600, flexShrink: 0 } }, roundName(r));
        if (st.kind === 'pending' && st.note) {
          return h('div', { key: r.round_id, style: { display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 } },
            label,
            h('span', { title: '待处理', style: { color: 'var(--dsw-alias-state-warn-primary)', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 } }, st.note));
        }
        return h('div', { key: r.round_id, style: { display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 } },
          label,
          stateChip(st.kind),
          st.note ? h('span', { style: { color: 'var(--dsw-alias-label-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 } }, st.note) : null);
      }
      // 单元级：一条紧凑汇总。两轮都通过 → 一行"全通过"；否则只点出没过的节点。
      function renderRounds(comparison) {
        const rounds = ((comparison && comparison.rounds) || []).filter((r) => r && r.round_id !== 'sample');
        if (!rounds.length) return h('div', { style: T.hint }, '无审核轮次记录');
        const states = rounds.map((r) => ({ r, st: nodeState(r) }));
        const allOk = states.every((x) => x.st.kind === 'ok');
        const box = { background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 8, padding: '7px 10px', marginBottom: 10, fontSize: 12 };
        if (allOk) {
          return h('div', { style: box },
            h('span', { style: { color: 'var(--dsw-alias-label-primary)', fontWeight: 600 } }, '✓ 一审通过'));
        }
        return h('div', { style: box },
          h('div', { style: { display: 'flex', flexDirection: 'column', gap: 3 } },
            states.map(({ r, st }) => roundNodeRow(r, st))));
      }
      // 单条问题：只显示"这一字段"上一审的结果，且跳过通过/未涉及的节点。
      function renderFindingRounds(finding) {
        const comp = finding && finding.comparison;
        const rounds = ((comp && comp.rounds) || []).filter((r) => r && r.round_id !== 'sample');
        if (!rounds.length) return null;
        const items = [];
        let anyJudged = false; // 该字段是否至少有一轮真实判定
        for (const r of rounds) {
          const chs = (r.checks || []).filter((c) => c && c.verdict !== undefined);
          // 该字段本身在这一轮没有判定（如该轮整体"历史来源未验证/待重新审核"）时，
          // 不在每条问题下重复输出——这类整体状态只在上方单元级汇总里显示一次。
          if (!chs.length) continue;
          anyJudged = true;
          const st = nodeState(r);
          if (st.kind === 'ok' || st.kind === 'na') continue; // 通过/未涉及的节点也不重复
          items.push({ r, st });
        }
        if (!items.length) {
          // 该字段在各轮都无判定 → 交给上方单元级汇总显示，不在每条问题下重复；
          // 有真实判定且都通过 → 明确写"与该问题一致"。
          if (!anyJudged) return null;
          return h('div', { style: { ...T.hint, marginTop: 4, color: 'var(--dsw-alias-label-primary)' } }, '✓ 一审与该问题一致');
        }
        return h('div', { style: { marginTop: 4, display: 'flex', flexDirection: 'column', gap: 2, fontSize: 12 } },
          items.map(({ r, st }) => roundNodeRow(r, st)));
      }

      // 知识点：多选（简化为逗号输入 + 快捷树）
      const knowledgeStr = (labels?.knowledge || []).join(', ');

      // ---- 中栏：题组列表（过滤 + 搜索 + 键盘高亮）----
      // 列表筛选只保留「全部 / 待审」两个入口。
      const filterTabs = [['all', '全部'], ['open', '待审']];
      const listCol = h('div', { className: 'wf-review-list-col', style: { flex: '0 1 220px', minWidth: 140, borderRight: '1px solid var(--dsw-alias-border-l1)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--dsw-alias-bg-layer-1)' } },
        h('div', { style: { padding: '10px 10px 8px', borderBottom: '1px solid var(--dsw-alias-border-l1)' } },
          h('div', { style: { display: 'flex', gap: 4, flexWrap: 'nowrap', marginBottom: 8 } },
            filterTabs.map(([id, lbl]) => h('button', {
              key: id, onClick: () => setFilter(id),
              style: {
                flex: '1 1 0', minWidth: 0, padding: '3px 3px', borderRadius: 7, fontSize: 11.5, cursor: 'pointer',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', textAlign: 'center',
                border: '1px solid var(--dsw-alias-border-l2)',
                background: filter === id ? 'var(--wf-sel-bg)' : 'transparent',
                border: '1px solid ' + (filter === id ? 'var(--wf-sel-border)' : 'var(--dsw-alias-border-l2)'),
                color: filter === id ? 'var(--dsw-alias-label-primary)' : 'var(--dsw-alias-label-secondary)',
              },
            }, lbl))),
          h('input', { className: 'wf-input', style: { ...T.input, width: '100%', padding: '5px 9px' }, value: q, placeholder: '题号 / 题型 / 知识点', onChange: (e) => setQ(e.target.value) }),
        ),
        h('div', { className: 'wf-scroll', ref: listScrollRef, style: { flex: 1, overflow: 'auto', padding: '7px 7px 3px' } },
          filteredRows.length === 0 ? h('div', { style: { ...T.hint, padding: 12 } }, '没有符合条件的题组') : filteredRows.map((r) => {
            const uid = r.unit_id || r.number;
            const active = uid === unitId;
            const hasIssue = r.needs_human || r.state === 'attention' || r.state === 'blocked';
            return h('div', {
              key: uid, className: 'wf-paper' + (active ? ' wf-active' : ''),
              'data-unit': uid,
              style: T.paperRow(active),
              onClick: () => onSelectUnit(uid),
            },
              // 题号列只放原卷印刷题号：无号题组用「—」，不能把大题标题（读起来像题干）塞进题号列。
              h('span', { style: { width: 44, flexShrink: 0, fontWeight: 600, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: Array.isArray(r.printed_numbers) && r.printed_numbers.length === 0 ? String((r.source_section || {}).title || '') : '' },
                Array.isArray(r.printed_numbers) && r.printed_numbers.length === 0
                  ? '—'
                  : String(r.label || r.number)),
              // 题号列表只展示目录主题型（语音题/单词拼写/阅读理解…），不展示子类、子题或题干；
              // 原卷大题标题只作悬停依据，题型缺失明确显示未判定。
              h('span', { title: r.source_section ? [r.source_section.context, r.source_section.title].filter(Boolean).join(' · ') : '', style: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 11.5, opacity: .85 } },
                String(r.type || '').trim() || '未判定'),
              r.excluded
                ? h('span', { style: { ...T.badge(''), flexShrink: 0, fontSize: 10.5 } }, '听力跳过')
                : h('span', {
                  className: 'wf-review-row-dot', role: 'img',
                  'aria-label': hasIssue ? '有审核问题' : (r.state === 'unverified' ? '尚未核对' : '无待审问题'),
                  title: hasIssue ? (r.open_summary || '有审核问题，请查看详情') : undefined,
                  style: { width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                    background: hasIssue ? 'var(--dsw-alias-state-warn-primary)' : 'var(--dsw-alias-label-primary)' },
                }),
            );
          })),
      );

      // ---- 右栏：工作区 ----
      let workCol;
      if (err) {
        workCol = h(EmptyState, {
          flex: true, tone: 'error', title: '加载失败：' + err,
          action: h('button', { className: 'wf-btn', style: { ...T.btn, marginTop: 12 }, onClick: () => load({ force: true }) }, '重试本题'),
        });
      } else if (!detail) {
        workCol = h(EmptyState, { flex: true, title: TEXT.loading });
      } else {
        workCol = h('div', { style: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 } },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px', height: 44, borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', flexShrink: 0, flexWrap: 'nowrap', overflow: 'hidden' } },
            h('span', {
              style: { fontWeight: 600, fontSize: 14.5, whiteSpace: 'nowrap', flexShrink: 0 },
              title: detail.source_section ? [detail.source_section.context, detail.source_section.title].filter(Boolean).join(' · ') : '',
            }, detail.printed_numbers && detail.printed_numbers.length === 0 ? '原卷未标小题号' : '第 ' + (detail.label || '') + ' 题'),
            detail.source_section && h('span', {
              title: [detail.source_section.context, detail.source_section.title].filter(Boolean).join(' · '),
              style: { ...T.hint, whiteSpace: 'nowrap', flexShrink: 0 },
            }, '原卷第' + detail.source_section.occurrence + '处大题'),
            detail.row && detail.row.excluded
              ? h('span', { style: { ...T.badge(''), flexShrink: 0, whiteSpace: 'nowrap' } }, '听力跳过')
              : (detail.needs_human
                ? h('span', { style: { ...T.badge('warn'), flexShrink: 0, whiteSpace: 'nowrap' }, title: (detail.row && detail.row.open_summary) || (detail.open_summary || '') || '标签与本轮审核判断不一致，需人工核对' }, '待审标签')
                : h('span', { style: { ...T.badge(''), flexShrink: 0, whiteSpace: 'nowrap' } }, '无待审')),
            (() => {
              const srcType = String((detail.labels && detail.labels.source_type) || (detail.item && detail.item.source_type) || '').trim();
              const yearVal = String((detail.labels && detail.labels.year) || (detail.item && detail.item.year) || '').trim();
              const diffVal = String(
                (detail.labels && detail.labels.difficulty)
                || (detail.preview_labels && detail.preview_labels.difficulty)
                || (detail.row && detail.row.difficulty)
                || ''
              ).trim();
              const rawCats = (detail.labels && Array.isArray(detail.labels.categories) && detail.labels.categories)
                || (detail.item && detail.item.filterMetadata && Array.isArray(detail.item.filterMetadata.categories) && detail.item.filterMetadata.categories)
                || [];
              const tags = [];
              const seen = new Set();
              const pushTag = (text, title) => {
                const v = String(text || '').trim();
                if (!v || v === '未填写' || seen.has(v)) return;
                seen.add(v);
                tags.push({ text: v, title });
              };
              pushTag(srcType, '来源：' + srcType);
              pushTag(yearVal, '年份：' + yearVal);
              pushTag(diffVal, '难度：' + diffVal);
              rawCats.forEach((c) => pushTag(c, '分类：' + c));
              if (!tags.length) return null;
              return h('div', {
                style: { display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0, overflow: 'hidden', flexShrink: 1 },
              },
                tags.map((t, i) => h('span', {
                  key: 'meta-' + i,
                  style: { ...T.badge('info'), flexShrink: 0, whiteSpace: 'nowrap' },
                  title: t.title,
                }, t.text)),
              );
            })(),
            h('span', { style: { flex: 1, minWidth: 4 } }),
            labelNotice ? h('span', { role: 'status', style: { ...T.hint, whiteSpace: 'nowrap', flexShrink: 0 } }, labelNotice) : null,
            h('button', {
              className: 'wf-btn-primary',
              style: {
                ...T.btnPrimary,
                padding: '5px 14px',
                fontSize: 13,
                fontWeight: 600,
                borderRadius: 6,
                background: '#2563eb',
                border: '1px solid #1d4ed8',
                color: '#ffffff',
                boxShadow: '0 1px 4px rgba(37, 99, 235, 0.28)',
                letterSpacing: '0.2px',
                whiteSpace: 'nowrap',
                flexShrink: 0,
              },
              title: '按 Word 试卷排版查看整套试卷预览',
              onClick: openPaperPreview,
            }, '整卷预览'),
          ),
          h('div', { className: 'wf-scroll wf-review-container' },
           h('div', { className: 'wf-review-layout' },
            // 左：原卷预览 + 小题
            h('div', { className: 'wf-review-main', style: { minWidth: 0 } },
              h('div', { style: T.card },
                h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 10 } },
                  h('span', { style: { fontSize: 13.5, fontWeight: 600, whiteSpace: 'nowrap' } }, TEXT.sourcePreview),
                ),
                (detail.audio || []).map((a, i) => h('audio', { key: (a.url || i) + '|' + (typeof MEDIA_ORIGIN === 'string' ? MEDIA_ORIGIN : ''), controls: true, preload: 'metadata', src: mediaUrl(a.url), style: { width: '100%', margin: '4px 0 8px' } })),
                h('div', { className: 'wf-paper' }, renderBlocks(detail.preview_blocks, detail.paper_bulk_bold)),
                // 「原卷修改」入口：放在原卷预览卡片右下角，点击弹出修改弹窗。
                h('div', { style: { display: 'flex', justifyContent: 'flex-end', marginTop: 12 } },
                  h('button', {
                    className: 'wf-btn-primary', style: T.btnPrimary, disabled: !content,
                    onClick: () => setContentDlg(true),
                  }, '原卷修改'),
                ),
              ),
              // 内容预览紧随原卷：先看原文，再看文章大意与解析；只读，不在这里改内容。
              h(ContentPreviewCard, {
                key: 'content:' + String(detail.unit_id || detail.label || ''),
                detail,
                onEdit: () => { setContentEditError(''); setContentEditDlg(true); },
              }),
            ),
            // 右：悬浮等宽三按钮 + 标签审核（对比）+ 审核说明（差异说明 + 修改/通过/驳回按钮）
            h('div', { className: 'wf-review-notes' },
              h('div', { style: { display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 8 } },
                h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, width: '100%' } },
                  h('button', {
                    className: 'wf-btn',
                    style: { ...T.btn, width: '100%', padding: '5px 8px', fontSize: 12, textAlign: 'center', whiteSpace: 'nowrap' },
                    disabled: !!aiBusy,
                    title: '为本批非听力/非写作缺解析或已标注AI解析的题目生成/重新生成解析（≤50字）',
                    onClick: () => runAiSupplement('explanation'),
                  }, aiBusy === 'explanation' ? '生成中…' : 'AI解析'),
                  h('button', {
                    className: 'wf-btn',
                    style: { ...T.btn, width: '100%', padding: '5px 8px', fontSize: 12, textAlign: 'center', whiteSpace: 'nowrap' },
                    disabled: !!aiBusy,
                    title: '为本批长材料题组缺失或已标注AI的大意生成/重新生成概括（≤50字）',
                    onClick: () => runAiSupplement('articleSummary'),
                  }, aiBusy === 'articleSummary' ? '生成中…' : 'AI文章大意'),
                  h('button', {
                    className: 'wf-btn',
                    style: { ...T.btn, width: '100%', padding: '5px 8px', fontSize: 12, textAlign: 'center', whiteSpace: 'nowrap' },
                    disabled: !!aiBusy,
                    title: '根据本地录音转写为本批缺听力原文的听力题去播报、去两遍重复并补全（标注 AI转录）',
                    onClick: () => runAiSupplement('listeningScript'),
                  }, aiBusy === 'listeningScript' ? '转录中…' : '听力材料补全'),
                ),
                (aiModal && aiModal.status === 'running' && !aiModalOpen)
                  ? h('button', {
                      type: 'button',
                      className: 'wf-btn',
                      style: { ...T.btn, alignSelf: 'flex-start', fontSize: 12, padding: '3px 10px' },
                      onClick: () => setAiModalOpen(true),
                    }, `后台生成中（${Number(aiModal.completed || 0)}/${Number(aiModal.total || 0)}）· 查看进度`)
                  : null,
                aiNotice
                  ? h('span', { role: 'status', style: { ...T.badge(aiNotice.startsWith('补充失败') ? 'err' : 'ok'), alignSelf: 'flex-start', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: aiNotice }, aiNotice)
                  : null,
              ),
              h('div', { style: T.card },
                h('div', { style: T.cardTitle }, '标签审核'),
                ...labelAuditPanels(detail),
              ),
              // 审核说明：只描述一审/二审与原标签（及各轮之间）的差异；按钮放在说明下面
              (() => {
                const lines = auditNotes(detail);
                const kpDiff = hasKnowledgeDiff(detail);
                const decideAll = async (status) => {
                  setBusy(status);
                  try {
                    const ids = [];
                    (groupedFindings || []).forEach((g) => { (g.ids && g.ids.length ? g.ids : [g.finding_id]).forEach((id) => { if (id) ids.push(id); }); });
                    (fmtFindings || []).forEach((f) => { if (f.finding_id) ids.push(f.finding_id); });
                    for (const id of ids) await jpost('/batches/' + encodeURIComponent(batchName) + '/decisions', { item: { finding_id: id, status, note: '' } });
                    setBusy(''); load();
                  } catch (e) { setErr(String(e.message || e)); setBusy(''); }
                };
                return h('div', { style: T.card },
                  h('div', { style: { ...T.cardTitle } }, '审核说明'),
                  lines.length
                    ? lines.map((t, i) => {
                        const isDiffNote = /^(?:题型差异|子类差异)：/.test(String(t || ''));
                        return h('div', {
                          key: i,
                          style: {
                            fontSize: 12,
                            color: isDiffNote ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-primary)',
                            fontWeight: isDiffNote ? 600 : 400,
                            padding: '3px 0',
                            lineHeight: 1.6,
                            borderTop: i ? '1px dashed var(--dsw-alias-border-l1)' : 'none',
                          },
                        }, '· ' + t);
                      })
                    : h('div', { style: T.hint }, kpDiff ? '差异已在上方「知识点标签」中标出。' : '各轮与原标签一致，无差异。'),
                  h('div', { style: { marginTop: 12, display: 'flex', gap: 8, justifyContent: 'stretch', borderTop: '1px solid var(--dsw-alias-border-l1)', paddingTop: 10 } },
                    h('button', { className: 'wf-btn', style: { ...T.btn, flex: 1, textAlign: 'center' }, onClick: () => setLabelDlg(true) }, '修改'),
                    h('button', { className: 'wf-btn', style: { ...T.btn, flex: 1, textAlign: 'center' }, disabled: busy === 'accepted', onClick: () => decideAll('accepted') }, busy === 'accepted' ? '处理中…' : '通过'),
                    h('button', { className: 'wf-btn-danger', style: { ...T.btnDanger, flex: 1, textAlign: 'center' }, disabled: busy === 'rejected', onClick: () => decideAll('rejected') }, busy === 'rejected' ? '处理中…' : '驳回'),
                  ),
                );
              })(),
            ),
           ),
          ),
          labelDlg ? h(LabelModal, {
            tax, labels, setL, knowledgeStr, warnings: detail.label_warnings || [],
            unitLabel: detail.label, unitKind: detail.unit_kind, numbers: detail.numbers,
            busy, saveError: labelError, onSave: saveLabels, onClose: () => { setLabels(detail.labels || {}); setLabelError(''); setLabelDlg(false); },
          }) : null,
          (aiModal && aiModalOpen) ? (() => {
            const title = aiModal.mode === 'listeningScript'
              ? 'AI 听力材料转录补全'
              : (aiModal.mode === 'articleSummary' ? 'AI 文章大意批量生成' : 'AI 解析批量生成');
            const total = Number(aiModal.total || 0);
            const completed = Number(aiModal.completed || 0);
            const pct = aiModal.status === 'done' ? 100 : (total > 0 ? (completed >= total ? 100 : Math.min(99, Math.round((completed / total) * 100))) : 12);
            const barColor = aiModal.status === 'error'
              ? 'var(--dsw-alias-state-error-primary)'
              : (aiModal.status === 'done' ? 'var(--dsw-alias-state-success-primary)' : 'var(--dsw-alias-brand-primary)');
            return h(ModalShell, {
              title,
              tag: aiModal.status === 'running' ? '生成中' : (aiModal.status === 'done' ? '已完成' : '失败'),
              width: 420,
              onClose: () => setAiModalOpen(false),
              footer: h('button', {
                className: aiModal.status === 'done' ? 'wf-btn-primary' : 'wf-btn',
                style: aiModal.status === 'done' ? T.btnPrimary : T.btn,
                onClick: () => {
                  setAiModalOpen(false);
                  if (aiModal.status !== 'running') setAiModal(null);
                },
              }, aiModal.status === 'running' ? '后台运行并关闭' : '完成'),
            },
              h('div', { style: { display: 'flex', flexDirection: 'column', gap: 12 } },
                (aiModal.model_display || aiModal.model) ? h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '6px 10px', borderRadius: 6, background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l1)', fontSize: 12 } },
                  h('span', { style: T.hint }, '当前模型'),
                  h('span', { style: { color: 'var(--dsw-alias-label-primary)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: String(aiModal.model_display || aiModal.model) },
                    String(aiModal.model_display || aiModal.model) + (aiModal.model_source === 'custom' ? '（自定义）' : (aiModal.model_source ? '（跟随默认）' : ''))),
                ) : null,
                h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12.5 } },
                  h('span', { style: { color: 'var(--dsw-alias-label-primary)', fontWeight: 500 } },
                    aiModal.status === 'running' ? (aiModal.message || '正在调用 AI 生成…')
                      : (aiModal.status === 'done' ? '生成完成' : '生成失败')),
                  h('span', { style: { ...T.hint, fontVariantNumeric: 'tabular-nums' } },
                    total > 0 ? `${completed} / ${total} 项 (${pct}%)` : `${pct}%`),
                ),
                h('div', { style: { height: 8, borderRadius: 999, background: 'var(--dsw-alias-bg-layer-2)', overflow: 'hidden', border: '1px solid var(--dsw-alias-border-l1)' } },
                  h('div', { style: { height: '100%', width: pct + '%', background: barColor, borderRadius: 999, transition: 'width .25s ease' } }),
                ),
                aiModal.status === 'done'
                  ? h('div', { style: { padding: '9px 12px', borderRadius: 8, background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l1)', fontSize: 12.5, color: 'var(--dsw-alias-label-primary)', lineHeight: 1.6 } },
                      '✓ ' + String(aiModal.message || `已完成 ${aiModal.applied || 0} 项生成并标注 AI 生成`) + '（均控制在 50 字以内；如需重生成可再次点击按钮覆盖）。')
                  : null,
                aiModal.status === 'error'
                  ? h('div', { style: { padding: '9px 12px', borderRadius: 8, background: 'rgba(220,38,38,.08)', border: '1px solid var(--dsw-alias-state-error-primary)', fontSize: 12.5, color: 'var(--dsw-alias-state-error-primary)', lineHeight: 1.6 } },
                      String(aiModal.error || aiModal.message || '生成失败'))
                  : null,
              ),
            );
          })() : null,
          (contentDlg && content) ? h(ContentModal, {
            content, detail, busy, dirty: contentDirty,
            onSub: setSubField, onUnit: setUnitField,
            onSave: saveContent, onClose: closeContent, saveError: contentError,
          }) : null,
          contentEditDlg ? h(ContentEditModal, {
            detail, busy, saveError: contentEditError,
            onSave: saveContentEdit,
            onClose: () => setContentEditDlg(false),
          }) : null,
          paperDlg ? h(PaperPreviewModal, {
            batchName, data: paperData, loading: paperLoading, error: paperError,
            onReload: openPaperPreview,
            onClose: () => setPaperDlg(false),
          }) : null,
        );
      }

      return h('div', { style: { flex: 1, display: 'flex', minHeight: 0 } }, listCol, workCol);
    }

    // ======================================================================
    // 试卷审核视图：列表 + 统计（进入后切换到审阅）
    // ======================================================================
    function WorkbenchTab(props) {
      const React = require('react');
      const h = React.createElement;
      const isActive = props.isActive !== false;
      const [batches, setBatches] = React.useState(() => (typeof SHARED_BATCHES_CACHE !== 'undefined' && (SHARED_BATCHES_CACHE.full || SHARED_BATCHES_CACHE.lite)) || null);
      const [active, setActive] = React.useState(null); // {name, unitId}
      const [err, setErr] = React.useState('');
      const [del, setDel] = React.useState(null); // 彻底删除确认弹层 {name,value,busy,error}
      const [advBusy, setAdvBusy] = React.useState('');
      const [uploadStates, setUploadStates] = React.useState({});
      const [uploadNotice, setUploadNotice] = React.useState('');
      const uploadStatesRef = React.useRef({});
      const uploadGeneration = React.useRef({});
      const uploadChecks = React.useRef({});
      function setUploadState(name, value) {
        const previous = uploadStatesRef.current[name];
        const next = { ...uploadStatesRef.current, [name]: value };
        uploadStatesRef.current = next;
        setUploadStates(next);
        if (value.message && (value.attempted || value.state === 'failed' || value.state === 'idle') && (!previous || previous.state !== value.state)) setUploadNotice(name);
      }
      async function checkUpload(name) {
        function uploadMessage(value, fallback) {
          if (value == null) return fallback || '';
          if (typeof value === 'object') return uploadMessage(value.message || value.error || value.detail, fallback);
          const text = String(value).trim();
          if (!text) return fallback || '';
          if (/^[\[{]/.test(text)) {
            try { return uploadMessage(JSON.parse(text), fallback); } catch (_) { return fallback || '请检查后端日志并重试。'; }
          }
          return text;
        }
        const generation = (uploadGeneration.current[name] || 0);
        if (uploadChecks.current[name] === generation) return;
        uploadChecks.current[name] = generation;
        try {
          const st = await jget('/batches/' + encodeURIComponent(name) + '/upload-status');
          if (generation !== (uploadGeneration.current[name] || 0)) return;
          const previous = uploadStatesRef.current[name];
          const record = (value) => setUploadState(name, { ...value, requiresUnreviewedConfirmation: st.requires_unreviewed_confirmation, unreviewedRevision: st.unreviewed && st.unreviewed.revision });
          const state = st.state || (st.running ? 'running' : st.ok === false ? 'failed' : 'idle');
          // An empty status after submission cannot prove the request did not reach the server.
          if (state === 'idle' && previous && previous.attempted) {
            setUploadState(name, { state: 'unknown', attempted: true, message: '暂时无法确认上传结果，请在远端核实，勿重复上传。' });
          } else if (state === 'running') {
            setUploadState(name, { state, attempted: true, message: st.step || '正在处理上传…' });
          } else if (state === 'accepted') {
            const receipt = st.receipt && typeof st.receipt === 'object' ? uploadMessage(st.receipt.id, '') : uploadMessage(st.receipt, '');
            const attachments = st.receipt && Array.isArray(st.receipt.attachments) ? st.receipt.attachments : [];
            const attachmentText = attachments.length
              ? ' 审核附件已上传：' + attachments.length + ' 个（' + attachments.join('、') + '）。'
              : '';
            setUploadState(name, { state, attempted: true, message: '已提交到云端上传暂存，等待远端确认。' + attachmentText + (receipt ? ' 回执：' + receipt : '') });
            if (!previous || previous.state !== 'accepted') loadBatches();
          } else if (state === 'failed') {
            const error = uploadMessage(st.error || st.step, '未知错误');
            // Prepare reports are already summarized by the server; keep readable output.
            const output = uploadMessage(st.output, '');
            const detail = output && !/^上传准备未通过：\d+ 项阻断错误/.test(String(error)) && !String(error).includes(output) ? '\n' + output : '';
            record({ state, message: '上传未完成：' + error + detail });
          } else if (state === 'idle') {
            record({ state: 'idle', message: '' });
          } else {
            setUploadState(name, { state: 'unknown', attempted: true, message: '上传结果未知，请在远端核实，勿重复上传。' + (st.error ? ' ' + uploadMessage(st.error, '') : '') });
          }
        } catch (e) {
          if (generation !== (uploadGeneration.current[name] || 0)) return;
          const previous = uploadStatesRef.current[name];
          setUploadState(name, { state: 'unknown', attempted: !!(previous && previous.attempted), message: '无法连接后端确认上传状态，请检查网络并等待重连；确认结果前勿重复上传。' });
        } finally {
          if (uploadChecks.current[name] === generation) delete uploadChecks.current[name];
        }
      }
      const [menuFor, setMenuFor] = React.useState(''); // 卡片「⋯」菜单当前展开的批次
      // 列表加载态：'idle' | 'loading' | 'ready' | 'error'。若已有共享缓存则直接就绪，避免切换标签闪出加载页。
      const [listState, setListState] = React.useState(() => (typeof SHARED_BATCHES_CACHE !== 'undefined' && (SHARED_BATCHES_CACHE.full || SHARED_BATCHES_CACHE.lite)) ? 'ready' : 'idle');
      const loadSeq = React.useRef(0);

      const loadBatches = React.useCallback(async () => {
        // 每次加载递增序号，只让最新一次的结果落地（防止重试/手动刷新交叉覆盖）。
        const seq = ++loadSeq.current;
        setListState((s) => (s === 'ready' ? s : 'loading'));
        setErr('');
        // 冷启动/后端重启时首包可能 5xx 或短暂连不上：指数退避重试几次，
        // 让面板自动“缓过来”，而不是把空列表丢给用户。
        const delays = [0, 600, 1200, 2000, 3000];
        let lastErr = null;
        for (let attempt = 0; attempt < delays.length; attempt++) {
          if (seq !== loadSeq.current) return; // 已被更新的请求取代
          if (delays[attempt]) await new Promise((r) => setTimeout(r, delays[attempt]));
          try {
            const d = await jget('/batches');
            if (seq !== loadSeq.current) return;
            const list = d.batches || [];
            if (typeof SHARED_BATCHES_CACHE !== 'undefined') { SHARED_BATCHES_CACHE.full = list; SHARED_BATCHES_CACHE.lite = list; }
            setBatches(list);
            setActive((current) => {
              if (!current) return current;
              const latest = (d.batches || []).find((b) => b.name === current.name);
              if (!latest) return current;
              // overview includes row status, findings and stale-stage state; revision clocks
              // can miss candidate edits, so always replace it while preserving selection.
              return { ...current, overview: latest };
            });
            setListState('ready');
            setErr('');
            return;
          } catch (e) {
            lastErr = e;
            if (seq !== loadSeq.current) return;
            // 只有可重试的错误（网络/超时/5xx）才继续退避；4xx（如 404）直接放弃。
            const msg = String((e && e.message) || e);
            const retriable = !/\b(400|401|403|404)\b/.test(msg);
            if (!retriable) break;
          }
        }
        if (seq !== loadSeq.current) return;
        setListState('error');
        setErr(String((lastErr && lastErr.message) || lastErr || '加载失败'));
      }, []);
      const hasActiveWork = (batches || []).some((b) =>
        (b && !b.flow_done && !b.uploaded && !b.queue_removed)
        || (b && b.advance && (b.advance.running || b.advance.queued || b.advance.waiting))
        || (b && b.upload_status && b.upload_status.running),
      );
      React.useEffect(() => {
        if (!isActive) {
          setActive(null);
          return;
        }
        if (typeof SHARED_BATCHES_CACHE !== 'undefined' && (SHARED_BATCHES_CACHE.full || SHARED_BATCHES_CACHE.lite)) {
          const cached = SHARED_BATCHES_CACHE.full || SHARED_BATCHES_CACHE.lite;
          setBatches((prev) => prev || cached);
          setListState((s) => (s === 'idle' || s === 'loading' ? 'ready' : s));
        }
        loadBatches();
        return () => { loadSeq.current += 1; };
      }, [loadBatches, isActive, props.uploadedTick]);
      const inReview = !!active;
      React.useEffect(() => {
        if (!isActive || inReview) return;
        const intervalMs = hasActiveWork ? 2500 : 8000;
        const timer = setInterval(() => {
          if (typeof document === 'undefined' || document.visibilityState !== 'hidden') loadBatches();
        }, intervalMs);
        return () => { clearInterval(timer); };
      }, [loadBatches, hasActiveWork, inReview, isActive]);
      // 窗口重新获得焦点时自动刷新一次（仅当前标签可见时）。
      React.useEffect(() => {
        if (!isActive) return;
        const onVisible = () => {
          if (typeof document !== 'undefined' && document.visibilityState === 'visible') loadBatches();
        };
        const onFocus = () => loadBatches();
        if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisible);
        if (typeof window !== 'undefined') window.addEventListener('focus', onFocus);
        return () => {
          if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisible);
          if (typeof window !== 'undefined') window.removeEventListener('focus', onFocus);
        };
      }, [loadBatches, isActive]);

      // Reconnect and page re-entry always consult durable status before enabling upload.
      React.useEffect(() => {
        if (!active) return;
        const name = active.name;
        if (!uploadStatesRef.current[name]) setUploadState(name, { state: 'checking', message: '正在查询上传状态…' });
        checkUpload(name);
        const timer = setInterval(() => checkUpload(name), 3000);
        return () => { clearInterval(timer); uploadGeneration.current[name] = (uploadGeneration.current[name] || 0) + 1; };
      }, [active && active.name]);

      function openBatch(name) {
        const prior = uploadStatesRef.current[name];
        setUploadState(name, { state: 'checking', attempted: !!(prior && prior.attempted), message: '正在查询上传状态…' });
        // 列表 /api/batches 已含每个批次的完整 rows（overview），直接取第一个单元进入；
        // 若当前仍是轻量缓存则优先复用悬停预取的首题 ID，再按需补拉单批详情。
        const b = (batches || []).find((x) => x.name === name);
        const rows = (b && b.rows) || [];
        const firstRow = rows[0];
        const cachedEntry = UNIT_CACHE.get(String(name || ''));
        const cachedFirstId = (cachedEntry && cachedEntry.loaded && Array.isArray(cachedEntry.order) && cachedEntry.order[0]) || '';
        const unitId = firstRow ? (firstRow.unit_id || firstRow.number) : (cachedFirstId || '_');
        setActive({ name, unitId, overview: b || null });
        if (!b || !Array.isArray(b.rows)) {
          jget('/batches/' + encodeURIComponent(name)).then((full) => {
            if (!full || typeof full !== 'object') return;
            const fullRows = full.rows || [];
            const first = fullRows[0];
            setActive((cur) => (cur && cur.name === name
              ? { ...cur, unitId: cur.unitId === '_' && first ? (first.unit_id || first.number) : cur.unitId, overview: full }
              : cur));
          }).catch(() => {});
        }
      }
      async function doAdvance(name) {
        setAdvBusy(name);
        try { await jpost('/batches/' + encodeURIComponent(name) + '/advance', {}); await loadBatches(); }
        catch (e) { setErr(String(e.message || e)); }
        finally { setAdvBusy(''); }
      }
      // 上传批次：把本批写入云端题库的“上传暂存”。后台执行并轮询状态；
      // 成功后后端 overview.uploaded=true，该批次会从「试卷审核」列表消失。
      async function doUpload(name) {
        const current = uploadStatesRef.current[name];
        if (!current || (current.state !== 'idle' && current.state !== 'failed') || typeof current.requiresUnreviewedConfirmation !== 'boolean') return;
        const unreviewed = current.requiresUnreviewedConfirmation;
        if (unreviewed && !current.unreviewedRevision) return;
        const ok = window.confirm('确定把「' + name + '」整批上传到云端题库？\n\n'
          + (unreviewed ? '警告：该批次尚未完成审核。确认继续将上传未经审核的原始知识点标签。' : '上传前会核对结构和审核状态。'));
        if (!ok) return;
        uploadGeneration.current[name] = (uploadGeneration.current[name] || 0) + 1;
        setUploadState(name, { state: 'running', attempted: true, message: '正在提交上传请求…' });
        try {
          if (unreviewed) await jpost('/batches/' + encodeURIComponent(name) + '/upload', { allow_unreviewed: true, unreviewed_revision: current.unreviewedRevision });
          else await jpost('/batches/' + encodeURIComponent(name) + '/upload', {});
        } catch (e) {
          if ([400, 401, 403, 404, 422].includes(e && e.httpStatus)) {
            window.alert('上传未开始：' + String(e.message || '请重新检查后再试。'));
            setUploadState(name, { state: 'checking', message: '正在重新检查上传状态…' });
            await checkUpload(name);
            return;
          }
          setUploadState(name, { state: 'unknown', attempted: true, message: '上传请求未收到确认。结果可能未知，请在远端核实，勿重复上传。' });
        }
        await checkUpload(name);
      }
      // 重新执行：tag=从「题型与子类」起重跑打标签到审核结束；review=从「审核」起重跑两轮审核到内容审核与上传格式检查结束。
      async function doRerun(name, mode) {
        const label = mode === 'tag' ? '重新标签（从题型与子类起，重跑到审核结束）' : '重新审核（从审核起，重跑到内容审核与上传格式检查结束）';
        if (!window.confirm('确定对「' + name + '」执行' + label + '？已完成的后续阶段会失效并重跑。')) return;
        setAdvBusy(name); setErr('');
        try {
          await jpost('/batches/' + encodeURIComponent(name) + '/rerun', { mode });
          // 后端返回 started=true/queued=true 后仍在后台排队或执行，持续跟踪并同步刷新进度。
          const deadline = Date.now() + 30 * 60 * 1000;
          let last;
          while (Date.now() < deadline) {
            await new Promise((r) => setTimeout(r, 2000));
            last = await jget('/batches/' + encodeURIComponent(name) + '/pipeline');
            loadBatches();
            if (!last || (!last.running && !last.queued && !last.waiting)) break;
          }
          if (last && (last.running || last.queued || last.waiting)) setErr('重新执行仍在后台运行，请稍后刷新查看；当前页面已停止等待。');
          await loadBatches();
        } catch (e) { setErr(String(e.message || e)); }
        finally { setAdvBusy(''); }
      }
      // 彻底删除确认：不使用 window.prompt（Electron/嵌入式 WebView 不弹原生输入框，
      // 会导致点击“没反应”）。改用页面内确认弹层，输入完整批次名后调用 purge。
      function openDelete(name) { setDel({ name, value: '', busy: false, error: '' }); }
      async function confirmDelete() {
        if (!del || del.busy) return;
        if (del.value !== del.name) { setDel({ ...del, error: '请输入完整批次名以确认删除' }); return; }
        setDel({ ...del, busy: true, error: '' });
        try {
          await jpost('/batches/' + encodeURIComponent(del.name) + '/purge', { confirm_name: del.name });
          setDel(null);
          await loadBatches();
          setActive(null);
        } catch (e) { setDel({ ...del, busy: false, error: String((e && e.message) || e) }); }
      }
      const deleteDialog = del ? h('div', {
        style: { position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(16,24,40,.42)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 },
        onClick: (e) => { if (e.target === e.currentTarget && !del.busy) setDel(null); },
      },
        h('div', {
          role: 'dialog', 'aria-modal': 'true', 'aria-label': '彻底删除批次确认',
          style: { width: '100%', maxWidth: 430, background: 'var(--dsw-alias-bg-layer-1)', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 14, padding: 18, boxShadow: '0 18px 48px rgba(16,24,40,.28)', display: 'flex', flexDirection: 'column', gap: 10 },
        },
          h('div', { style: { fontSize: 15, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' } }, '彻底删除批次'),
          h('div', { style: { ...T.hint, lineHeight: 1.6 } }, '将永久删除批次「' + del.name + '」的目录、收件箱副本与所有本地回收归档，无法恢复。请输入完整批次名确认：'),
          h('div', { style: { display: 'flex', gap: 8, alignItems: 'stretch' } },
            h('input', {
              className: 'wf-input', autoFocus: true, value: del.value,
              style: { ...T.input, flex: 1, minWidth: 0 },
              placeholder: del.name,
              onChange: (e) => setDel({ ...del, value: e.target.value, error: '' }),
              onKeyDown: (e) => { if (e.key === 'Enter') confirmDelete(); if (e.key === 'Escape' && !del.busy) setDel(null); },
            }),
            h('button', {
              type: 'button', className: 'wf-btn', style: { ...T.btn, flexShrink: 0, whiteSpace: 'nowrap' },
              disabled: del.busy,
              title: '一键填入完整批次名',
              onClick: () => setDel({ ...del, value: del.name, error: '' }),
            }, '一键填入'),
          ),
          del.error ? h('div', { role: 'alert', style: { ...T.statusErr, padding: '6px 9px', borderRadius: 8 } }, del.error) : null,
          h('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 } },
            h('button', { className: 'wf-btn', style: T.btn, disabled: del.busy, onClick: () => setDel(null) }, '取消'),
            h('button', { className: 'wf-btn-danger', style: T.btnDanger, disabled: del.busy || del.value !== del.name, onClick: confirmDelete }, del.busy ? '正在删除…' : '确认彻底删除'),
          ),
        ),
      ) : null;

      // 进入审阅：原工作台三栏布局（单元列表 + 工作区），键盘 ↑/↓ 切换题组。
      if (active) {
        const rows = (active.overview && active.overview.rows) || [];
        const upload = uploadStates[active.name] || { state: 'checking', message: '正在查询上传状态…' };
        return h(React.Fragment, null, h('div', { style: { flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 } },
          h('div', { style: T.toolbar },
            h('button', { className: 'wf-btn', style: T.btn, onClick: () => setActive(null) }, '← ' + TEXT.back),
            h('span', { style: T.toolbarTitle }, (active.overview?.paper?.title || active.name)),
            h('span', { style: { ...T.hint, marginLeft: 4 } }, '↑/↓ 或 k/j 切换题组'),
            h('span', { style: { flex: 1 } }),
            upload.state === 'idle' && typeof upload.requiresUnreviewedConfirmation !== 'boolean' ? h('span', { role: 'status', style: T.hint }, '无法确认审核状态，请更新后端并重新进入批次。') : null,
            upload.message && uploadNotice !== active.name && (upload.attempted || upload.state === 'failed' || upload.state === 'idle') ? h('button', { className: 'wf-btn', style: T.btn, onClick: () => setUploadNotice(active.name) }, '查看上传通知') : null,
            h('button', { className: 'wf-btn-primary', style: T.btnPrimary, onClick: () => doUpload(active.name), disabled: !['idle', 'failed'].includes(upload.state) || typeof upload.requiresUnreviewedConfirmation !== 'boolean' || advBusy === active.name }, upload.state === 'running' ? '上传中…' : '上传批次'),
            h('button', { className: 'wf-btn-danger', style: T.btnDanger, onClick: () => openDelete(active.name) }, TEXT.archive),
          ),
          uploadNotice === active.name && upload.message ? (() => {
            const isErrorNotice = upload.state === 'failed' || upload.state === 'unknown';
            const isLongNotice = isErrorNotice || String(upload.message).length > 120 || String(upload.message).includes('\n');
            const noticeTitle = upload.state === 'failed' ? '上传失败' : upload.state === 'unknown' ? '上传状态未知' : '上传通知';
            return h('section', {
              role: 'dialog', 'aria-label': noticeTitle,
              style: {
                position: 'fixed', right: 20, bottom: 20, zIndex: 2500,
                width: 'min(420px, calc(100vw - 32px))',
                height: isLongNotice ? 'min(280px, calc(100vh - 140px))' : 'auto',
                maxHeight: 'min(280px, calc(100vh - 140px))',
                display: 'flex', flexDirection: 'column', overflow: 'hidden',
                boxSizing: 'border-box', borderRadius: 12,
                border: '1px solid ' + (isErrorNotice ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-border-l2)'),
                background: 'var(--dsw-alias-bg-layer-1)',
                boxShadow: '0 10px 30px rgba(0,0,0,.18)',
              },
            },
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderBottom: '1px solid var(--dsw-alias-border-l1)', flexShrink: 0 } },
                h('strong', { style: { flex: 1, minWidth: 0, fontSize: 13.5, fontWeight: 600, color: isErrorNotice ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-primary)' } }, noticeTitle),
                h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0 }, onClick: () => { try { navigator.clipboard && navigator.clipboard.writeText(upload.message); } catch (_) {} }, title: '复制通知内容' }, '复制'),
                h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0 }, onClick: () => setUploadNotice(''), 'aria-label': '关闭上传通知' }, '关闭')),
              h('div', {
                className: 'wf-scroll',
                role: isErrorNotice ? 'alert' : 'status',
                'aria-live': isErrorNotice ? 'assertive' : 'polite',
                style: { flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden', padding: '10px 14px', fontSize: 12.5, lineHeight: 1.65, color: 'var(--dsw-alias-label-primary)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', wordBreak: 'break-word' },
              }, upload.message),
            );
          })() : null,
          h(ReviewView, {
            // 切题不重建组件：key 只随批次变化。若按 unitId 作 key，每次 ↑/↓
            // 都会重挂载，把题组列表与预览区的滚动条全部重置到顶端。
            key: 'review:' + active.name,
            batchName: active.name,
            rows,
            unitId: active.unitId,
            onSelectUnit: (uid) => setActive({ ...active, unitId: uid }),
          }),
        ), deleteDialog);
      }

      // 只保留右侧「试卷审核」卡片列表这一份，去掉左栏「需要审核的试卷」列表，
      // 避免同一批试卷在左右两列重复展示。
      // 已上传到云端（overview.uploaded）的批次视为完成，不再出现在审核列表。
      // 内容审核与上传格式检查结束（flow_done）的试卷排在最前并标「待人工确认」，与上传页的
      // 「已完成 N 份 → 前往人工确认」入口对应：处理中仍在队列，结束后流转到这里。
      const list = (batches || []).filter((b) => !b.uploaded)
        // 最新的在最上面：完成时间/最近进度/批次更新时间取最大值降序。
        .sort((a, b) => batchLatestTime(b) - batchLatestTime(a));
      return h(React.Fragment, null, h('div', { style: { flex: 1, display: 'flex', minHeight: 0 } },
        h('div', { style: T.main },
          h('div', { style: T.toolbar },
            h('span', { style: T.toolbarTitle }, TEXT.review),
            h('span', { style: { flex: 1 } }),
            h(BackendRestartControl, { onRestarted: loadBatches }),
            h('button', { className: 'wf-btn', style: T.btn, onClick: loadBatches }, TEXT.reload),
          ),
          err ? h('div', { style: { padding: 14, ...T.statusErr } },
            '试卷列表加载失败：' + err + ' ',
            h('button', { className: 'wf-btn', style: { ...T.btn, marginLeft: 10 }, onClick: loadBatches }, '重试')) : null,
          h('div', { className: 'wf-scroll', style: T.scroll },
            listState === 'loading' && list.length === 0
              ? h(EmptyState, { title: '正在加载试卷列表…' })
              : listState === 'error' && list.length === 0
                ? h(EmptyState, { tone: 'error', title: '暂时连不上后端，未能取到试卷列表。点上方「重试」再试一次。' })
                : list.length === 0
                  ? h(EmptyState, { title: TEXT.noPaper })
                  : list.map((b) => h('div', { key: b.name, className: 'wf-card-hover', style: { ...T.cardClickable, width: '94%', marginLeft: 'auto', marginRight: 'auto' }, onMouseEnter: () => { loadBatchUnitsBundle(b.name).catch(() => {}); }, onClick: () => openBatch(b.name) },
              // 第一行：标题 + 细字体统计（无底色，仅“待审”带底色）
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 10 } },
                h('div', { style: { ...T.paperCardTitle, flex: 1, minWidth: 0 } }, b.paper?.title || b.name),
                b.flow_done
                  ? h('span', { style: { flexShrink: 0, fontSize: 11, fontWeight: 600, color: 'var(--dsw-alias-label-primary)', background: 'var(--wf-sel-bg)', border: '1px solid var(--wf-sel-border)', borderRadius: 999, padding: '2px 9px', whiteSpace: 'nowrap' } }, '待人工确认')
                  : null,
                h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, flexShrink: 0, fontSize: 11.5, fontWeight: 400, color: 'var(--dsw-alias-label-secondary)', whiteSpace: 'nowrap' } },
                  h('span', null, (b.unit_total ?? 0) + ' 题组'),
                  h('span', null, (b.question_total ?? 0) + ' 小题'),
                  (b.needs_human ?? 0) > 0
                    ? h('span', { style: { color: 'var(--dsw-alias-state-warn-primary)', background: 'rgba(230,162,60,.12)', borderRadius: 999, padding: '1px 8px' } }, (b.needs_human ?? 0) + ' 待审')
                    : h('span', null, '0 待审'),
                ),
              ),
              flowTimingText(b) ? h('div', { style: { ...T.hint, marginTop: 6, fontVariantNumeric: 'tabular-nums' } }, flowTimingText(b)) : null,
              b.review_status ? h('div', { style: { ...T.hint, marginTop: 6 } },
                (b.review_status.rounds || []).map((r) => r.name + (r.completed ? ('已完成 ' + r.reviewed + (r.round_id === 'sample' ? ' 抽样核对' : ' 单元')) : r.state === 'needs_refresh' ? '已有审核记录，待更新' : '尚未执行')).join(' · ')
                + (b.review_status.execution_completed ? (' · ' + (b.review_status.quality_passed ? '审核无未决项' : (b.review_status.open_findings + ' 项待处理差异'))) : '')
                + (b.review_status.excluded_units ? (' · 听力跳过 ' + b.review_status.excluded_units + ' 题组') : '')) : null,
              // 第二行：批次/阶段 + 操作按钮
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 } },
                h('div', { style: { ...T.paperCardMeta, flex: 1, minWidth: 0 } }, '批次：' + b.name + (b.stage?.hint ? ' · ' + b.stage.hint : '')),
                h('div', { style: { display: 'flex', gap: 6, flexShrink: 0, position: 'relative' } },
                  h('button', { className: 'wf-btn-primary', style: { ...T.miniBtnPrimary, color: 'var(--wf-brand)' }, onClick: (e) => { e.stopPropagation(); openBatch(b.name); } }, '进入审核'),
                  h('button', {
                    className: 'wf-btn', style: { ...T.miniBtn, padding: '3px 9px', fontSize: 15, lineHeight: 1, fontWeight: 700, color: 'var(--dsw-alias-label-secondary)' },
                    title: '更多操作', 'aria-label': '更多操作', 'aria-expanded': menuFor === b.name,
                    onClick: (e) => { e.stopPropagation(); setMenuFor(menuFor === b.name ? '' : b.name); },
                  }, '⋯'),
                  menuFor === b.name ? h('div', { style: { display: 'contents' } },
                    // 透明遮罩：点菜单外任意处即收起，且不触发卡片进入审核。
                    h('div', { style: { position: 'fixed', inset: 0, zIndex: 40 }, onClick: (e) => { e.stopPropagation(); setMenuFor(''); } }),
                    h('div', {
                      style: { position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 41, minWidth: 132,
                        display: 'flex', flexDirection: 'column', gap: 2, padding: 5, borderRadius: 10,
                        background: 'var(--dsw-alias-bg-layer-1)', border: '1px solid var(--dsw-alias-border-l2)',
                        boxShadow: '0 10px 30px rgba(16,24,40,.16)' },
                      onClick: (e) => e.stopPropagation(),
                    },
                      [['重新标签', () => doRerun(b.name, 'tag'), false],
                       ['重新审核', () => doRerun(b.name, 'review'), false],
                       ["彻底删除", () => openDelete(b.name), true]].map(([label, fn, danger]) =>
                        h('button', {
                          key: label, className: 'wf-btn',
                          style: { ...T.miniBtn, textAlign: 'left', border: 'none', background: 'transparent',
                            color: danger ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-primary)' },
                          onClick: (e) => { e.stopPropagation(); setMenuFor(''); fn(); },
                        }, label))),
                  ) : null,
                ),
              ),
            )),
          ),
        ),
      ), deleteDialog);
    }

    // ======================================================================
    // 模型与设置
    // ======================================================================
    // 题库连接配置独立于模型路由；密钥仅提交给宿主，不保存在浏览器存储中。
    // 初始化引导：把「要装什么、要下什么、要配什么」收成一页，新用户照此走一遍即可。
    // 插件本体很小，听力运行时与模型按需下载，所以下载/配置都收成一个按钮。
    function OnboardingSection({ onNavigate }) {
      const React = require('react');
      const h = React.createElement;
      const [loading, setLoading] = React.useState(true);
      const [items, setItems] = React.useState([]);
      const [busy, setBusy] = React.useState('');
      const [msg, setMsg] = React.useState(null);
      const get = async (url) => {
        try {
          const r = await window.fetch(url, { headers: { accept: 'application/json' } });
          return await r.json();
        } catch (e) { return { error: String((e && e.message) || e) }; }
      };
      async function load() {
        setLoading(true);
        const [health, audio, ocr, cfg, bank] = await Promise.all([
          get('/wf-plugin/health'),
          get('/wf-plugin/api/audio/config'),
          get('/wf-plugin/ocr'),
          get('/wf-plugin/config'),
          get('/wf-plugin/question-bank'),
        ]);
        const ocrCfg = (ocr && ocr.config) || {};
        const router = (cfg && cfg.router) || {};
        const bankCfg = (bank && bank.config) || {};
        // OCR/题库的 endpoint/base_url 都有默认值，不能用它判断“配好了”；
        // 宿主只回 has_token / has_api_key（不回密钥本身），按这两个字段判定。
        const hasOcr = ocrCfg.has_token === true;
        const effectiveModel = (router.default && router.default.model) ? router.default : (router.harnessDefault || {});
        const hasModel = !!effectiveModel.model;
        const hasBank = bankCfg.has_api_key === true;
        setItems([
          { id: 'python', title: 'Python 运行时', required: true, ready: !!(health && health.python),
            detail: (health && health.python) ? health.python : '未找到可用 Python。插件通常自带便携版，缺失时请重新安装插件。',
            action: null },
          { id: 'ffmpeg', title: 'ffmpeg / ffprobe', required: true,
            ready: !!(audio && audio.ffmpeg && audio.ffprobe),
            detail: (audio && audio.ffmpeg) ? ('ffmpeg: ' + audio.ffmpeg) : '未检测到 ffmpeg。听力切段需要它（插件 vendor 目录或系统 PATH）。',
            action: null },
          { id: 'audio', title: '听力模型（WhisperX + 识别模型）', required: false,
            ready: !!(audio && audio.ready),
            detail: (audio && audio.ready) ? '已安装，可以直接切段。' : '未安装。插件本体不含模型；点下方按钮按需下载运行时与模型，首次约需数分钟。',
            action: 'install-audio' },
          { id: 'ocr', title: 'OCR 识别', required: false, ready: hasOcr,
            detail: hasOcr ? ('已配置 OCR 服务（' + (ocrCfg.endpoint || '') + '）。') : '未填 OCR Token。扫描件/图片型试卷和上传的 PDF（整卷识别产出 Word 版面参考）都需要 OCR；只传纯 Word 卷可跳过。',
            action: 'goto-ocr' },
          { id: 'model', title: '大模型', required: true, ready: hasModel,
            detail: hasModel ? ((router.default && router.default.model ? '默认模型：' : '跟随 Harness 当前模型：') + effectiveModel.model + (router.default && router.default.model ? '' : '（无需填写 API Key，凭据由 Harness 提供）')) : '未检测到可用模型。请在「模型与设置 → 模型设置」选择本机 Harness 已配置的模型；插件不需要额外填写 API Key。',
            action: 'goto-models' },
          { id: 'bank', title: '题库连接', required: false, ready: hasBank,
            detail: hasBank ? ('题库：' + bankCfg.base_url) : '未填题库 API Key。上传到题库前需要填服务地址、账号与密钥。',
            action: 'goto-api' },
        ]);
        setLoading(false);
      }
      React.useEffect(() => { load(); }, []);
      async function installAudio(force) {
        setBusy('audio');
        setMsg(force ? '正在更新听力运行时与模型（强制重新下载）…' : '正在安装听力运行时与模型；首次下载较大，请保持窗口打开…');
        try {
          const r = await window.fetch('/wf-plugin/api/audio/bootstrap', {
            method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json' },
            body: JSON.stringify({ force: !!force }),
          });
          const data = await r.json();
          if (!r.ok || data.error) throw new Error(data.error || '安装任务启动失败');
          setMsg('安装任务已启动，可在「听力切分」页查看进度。');
          await load();
        } catch (e) { setMsg('安装失败：' + String((e && e.message) || e)); }
        finally { setBusy(''); }
      }
      function act(item) {
        if (!item.action) return;
        // 已就绪时按钮是“更新模型”，用 force 真正重新下载，而不是被 READY 标记跳过。
        if (item.action === 'install-audio') return installAudio(item.ready);
        if (item.action === 'goto-ocr') return onNavigate('ocr');
        if (item.action === 'goto-models') return onNavigate('models');
        if (item.action === 'goto-api') return onNavigate('api');
      }
      const readyCount = items.filter((i) => i.ready).length;
      const requiredMissing = items.filter((i) => i.required && !i.ready);
      const badge = (ready) => h('span', { style: T.pill },
        h('span', { style: T.pillNum }, ready ? '\u2713' : '!'), ready ? '就绪' : '待完成');
      return h('section', { 'aria-labelledby': 'wf-onboarding-heading', style: { minWidth: 0 } },
        h('h2', { id: 'wf-onboarding-heading', style: T.cardTitle }, '初始化引导'),
        h('p', { className: 'wf-settings-description' },
          '插件本体很小，听力运行时与模型按需下载。新装插件后照着下面走一遍即可：标「必须」的项完成后才能跑完整流程，其余按需。'),
        loading ? h('div', { style: T.hint }, '正在检查环境…') : null,
        !loading ? h('div', { style: { ...T.card, boxShadow: 'none', marginBottom: 12 } },
          h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' } },
            h('strong', null, '完成进度'),
            h('span', { style: T.pill }, h('span', { style: T.pillNum }, readyCount + '/' + items.length), '项就绪'),
            requiredMissing.length
              ? h('span', { style: { ...T.hint, marginLeft: 'auto' } }, '还需完成：' + requiredMissing.map((i) => i.title).join('、'))
              : h('span', { style: { ...T.statusOk, marginLeft: 'auto' } }, '\u2713 必需项已就绪，可以开始使用')),
          h('div', { style: { marginTop: 10 } },
            h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: load }, '重新检查')),
        ) : null,
        msg ? h('div', { role: 'status', style: { ...(String(msg).startsWith('安装失败') ? T.statusErr : T.statusOk), marginBottom: 12 } }, msg) : null,
        h('div', { style: { display: 'grid', gap: 10 } },
          items.map((item) => h('div', { key: item.id, style: { ...T.card, boxShadow: 'none', display: 'grid', gap: 8 } },
            h('div', { style: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' } },
              h('strong', { style: { fontSize: 13.5 } }, item.title),
              h('span', { style: { ...T.pill, fontSize: 10.5 } }, item.required ? '必须' : '按需'),
              h('span', { style: { marginLeft: 'auto' } }, badge(item.ready))),
            h('div', { style: T.hint }, item.detail),
            item.action ? h('div', null,
              h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: () => act(item) },
                item.action === 'install-audio' ? (item.ready ? '重新安装 / 更新模型' : '安装运行时与模型') : '去配置')) : null,
          ))),
      );
    }

    function QuestionBankSettings() {
      const React = require('react');
      const h = React.createElement;
      const [config, setConfig] = React.useState(null);
      const [apiKey, setApiKey] = React.useState('');
      const [loginPassword, setLoginPassword] = React.useState('');
      const [busy, setBusy] = React.useState('');
      const [dirty, setDirty] = React.useState(false);
      const [sshDirty, setSshDirty] = React.useState(false);
      const [notice, setNotice] = React.useState(null);
      React.useEffect(() => {
        let active = true;
        requestSettings('GET').then((data) => { if (active) setConfig(data.config); })
          .catch((error) => { if (active) setNotice({ error: true, text: error.message }); });
        return () => { active = false; };
      }, []);
      async function requestSettings(method, payload, test) {
        const response = await window.fetch('/wf-plugin/question-bank' + (test ? '/test' : ''), {
          method, headers: { accept: 'application/json', ...(method === 'POST' ? { 'content-type': 'application/json' } : {}) },
          ...(method === 'POST' ? { body: JSON.stringify(payload) } : {}),
        });
        const data = await response.json();
        if (!response.ok || data.ok === false) throw new Error(data.error || data.message || '题库连接操作失败，请检查设置后重试。');
        return data;
      }
      function change(name, value) {
        setConfig((current) => ({ ...current, [name]: value })); if (name.startsWith('ssh_')) setSshDirty(true); setDirty(true); setNotice(null);
      }
      async function perform(action) {
        if (!config || busy) return;
        setBusy(action); setNotice(null);
        const payload = { base_url: config.base_url, subject_id: config.subject_id, timeout_seconds: Number(config.timeout_seconds), knowledge_version_id: config.knowledge_version_id || '', account: config.account || '' };
        if (sshDirty) Object.assign(payload, { ssh_host: config.ssh_host || '', ssh_port: Number(config.ssh_port), ssh_identity: config.ssh_identity || '', ssh_remote_port: Number(config.ssh_remote_port) });
        if (apiKey) payload.api_key = apiKey;
        if (loginPassword) payload.password = loginPassword;
        if (action === 'clear') { delete payload.api_key; payload.clear_api_key = true; }
        if (action === 'clear-password') { delete payload.password; payload.clear_password = true; }
        try {
          const data = await requestSettings('POST', payload, action === 'test');
          if (action === 'test') {
            setNotice({ error: false, text: data.message || '目录读取成功。上传仍需 draft 权限，此测试不会提交题目。' });
          } else {
            setConfig(data.config); setApiKey(''); setLoginPassword(''); setDirty(false); setSshDirty(false);
            setNotice({ error: false, text: action === 'clear' ? '已清除保存的 API Key。' : action === 'clear-password' ? '已清除保存的登录密码。' : '题库连接设置已保存。' });
          }
        } catch (error) { setNotice({ error: true, text: error.message }); }
        finally { setBusy(''); }
      }
      const field = (name, label, options, help) => h('label', { style: { display: 'grid', gap: 6, marginBottom: 16 } },
        h('span', { style: T.fieldLabel }, label),
        h('input', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, value: config?.[name] ?? '', disabled: !!busy, onChange: (event) => change(name, event.target.value), ...options }),
        help ? h('span', { style: T.hint }, help) : null);
      return h('section', { 'aria-labelledby': 'wf-question-bank-heading', style: { minWidth: 0 } },
        h('h2', { id: 'wf-question-bank-heading', style: T.cardTitle }, '上传设置'),
        h('p', { className: 'wf-settings-description' }, '上传使用具备暂存权限的 API Key，不需要题库账号密码。'),
        !config ? h('div', { style: { ...T.card, boxShadow: 'none' } },
          h('div', { style: T.hint }, notice?.error ? '配置加载失败，重新进入此页可重试。' : '正在读取上传设置…')) :
        h('div', { style: { display: 'grid', gap: 16 } },
          h('div', { style: T.card },
            h('h3', { style: { ...T.cardTitle, fontSize: 16, marginBottom: 12 } }, 'API'),
            field('base_url', 'API Base 地址', { type: 'url', placeholder: 'http://127.0.0.1:8089/api', autoComplete: 'off', spellCheck: false }, '填写以 /api 结尾的接口地址。默认走本机 SSH 隧道。'),
            h('label', { style: { display: 'grid', gap: 6, marginBottom: 16 } },
              h('span', { style: T.fieldLabel }, 'API Key'),
              h('input', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, type: 'password', value: apiKey, autoComplete: 'new-password', spellCheck: false, disabled: !!busy, placeholder: config.has_api_key ? '已保存密钥；留空保持不变' : '填写题库账号的 API Key', onChange: (event) => { setApiKey(event.target.value); setDirty(true); setNotice(null); } }),
              h('span', { style: T.hint }, config.has_api_key ? '密钥已保存，不回显。填入新值后保存即可替换。' : '使用 Bearer Token 认证。')),
            field('subject_id', '科目 ID', { type: 'text', placeholder: 'english', autoComplete: 'off' }, '应与 API Key 绑定账号的科目一致。英语默认 english。'),
            h('details', { style: { margin: '4px 0 0' } },
              h('summary', { style: { cursor: 'pointer', ...T.hint, padding: '4px 0', marginBottom: 12 } }, '高级参数'),
              field('timeout_seconds', '请求超时（秒）', { type: 'number', min: 1, max: 120, step: 1 }, '默认 30 秒。'),
              field('knowledge_version_id', '知识点版本 ID（可选）', { type: 'text', placeholder: '留空自动选择启用版本', autoComplete: 'off' }, '仅在有多个启用通用版本时填写。'))),
          h('div', { style: T.card },
            h('h3', { style: { ...T.cardTitle, fontSize: 16, marginBottom: 12 } }, '上传账户'),
            field('account', '上传账号（可选）', { type: 'text', placeholder: '例如 001 或主管理员账号', autoComplete: 'off', spellCheck: false }, '旧版人工会话配置，不参与上传暂存。'),
            h('label', { style: { display: 'grid', gap: 6, marginBottom: 0 } },
              h('span', { style: T.fieldLabel }, '登录密码（可选）'),
              h('input', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, type: 'password', value: loginPassword, autoComplete: 'new-password', spellCheck: false, disabled: !!busy, placeholder: config.has_password ? '已保存登录密码；留空保持不变' : '填写该账号的后台登录密码', onChange: (event) => { setLoginPassword(event.target.value); setDirty(true); setNotice(null); } }),
              h('span', { style: T.hint }, config.has_password ? '密码已保存，不回显；仅保存在本机 0600 私有文件。' : '仅保留旧版人工会话配置，不用于上传。'))),
          h('div', { style: T.card },
            h('h3', { style: { ...T.cardTitle, fontSize: 16, marginBottom: 12 } }, 'SSH 隧道'),
            h('div', { style: { ...T.hint, marginBottom: 14 } }, '已预填本机服务器与密钥路径；上传时自动建立隧道，不需要手工执行 ssh。'),
            field('ssh_host', 'SSH 登录地址', { type: 'text', placeholder: 'user@host', autoComplete: 'off', spellCheck: false }, '格式 user@host；仅更换服务器时修改。'),
            field('ssh_port', 'SSH 端口', { type: 'number', min: 1, max: 65535, step: 1 }, '默认 22。'),
            field('ssh_identity', '密钥文件路径', { type: 'text', placeholder: '本机私钥路径', autoComplete: 'off', spellCheck: false }, '填写本机私钥文件路径，不要粘贴私钥内容。'),
            field('ssh_remote_port', '远端端口', { type: 'number', min: 1, max: 65535, step: 1 }, '默认 89。保存后上传时自动建立。'))),
        h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 16 } },
          h('button', { type: 'button', className: 'wf-btn-primary', style: T.btnPrimary, disabled: !!busy, onClick: () => perform('save') }, busy === 'save' ? '正在保存…' : '保存设置'),
          h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: () => perform('test') }, busy === 'test' ? '正在测试…' : '测试连接'),
          config?.has_api_key ? h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: () => perform('clear') }, busy === 'clear' ? '正在清除…' : '清除密钥') : null,
          config?.has_password ? h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: () => perform('clear-password') }, busy === 'clear-password' ? '正在清除…' : '清除登录密码') : null),
        dirty && !busy ? h('div', { style: { ...T.hint, marginTop: 10 } }, '有未保存的改动；连接测试使用当前填写值。') : null,
        notice ? h('div', { role: notice.error ? 'alert' : 'status', 'aria-live': 'polite', style: { ...(notice.error ? T.statusErr : T.statusOk), marginTop: 12, overflowWrap: 'anywhere' } }, notice.text) : null,
        h('div', { style: { borderTop: '1px solid var(--dsw-alias-border-l1)', marginTop: 20, paddingTop: 16, display: 'grid', gap: 8 } },
          h('div', { style: T.hint }, '暂存权限以 API Key 的能力检查结果为准；确认、审核和发布仍在题库工作台完成。'),
          h('div', { style: T.hint }, 'SSH 隧道使用本机 ssh 与密钥，只连本机转发端口；密钥路径保存在本机私有配置中。'),
          h('div', { style: T.hint }, '公网 HTTP 文档地址仅供阅读，不能用于发送密钥。云端提交只写入上传暂存；人工确认入库和发布仍在题库后台完成。')));
    }

    function OCRSettings() {
      const React = require('react');
      const h = React.createElement;
      const [config, setConfig] = React.useState(null);
      const [token, setToken] = React.useState('');
      const [source, setSource] = React.useState('');
      const [sourceType, setSourceType] = React.useState('url');
      const [busy, setBusy] = React.useState('');
      const [switching, setSwitching] = React.useState(false);
      const [, setDirty] = React.useState(false);
      const [notice, setNotice] = React.useState(null);
      const [result, setResult] = React.useState(null);
      const [loading, setLoading] = React.useState(true);
      async function request(method, payload, test) {
        const response = await window.fetch('/wf-plugin/ocr' + (test ? '/test' : ''), {
          method, headers: { accept: 'application/json', ...(method === 'POST' ? { 'content-type': 'application/json' } : {}) },
          ...(method === 'POST' ? { body: JSON.stringify(payload) } : {}),
        });
        const data = await response.json();
        if (!response.ok || data.ok === false || data.error) throw new Error(data.error || data.message || 'OCR 操作失败，请检查配置后重试。');
        return data;
      }
      async function load() {
        setLoading(true); setNotice(null);
        try { const data = await request('GET'); setConfig(data.config); }
        catch (error) { setNotice({ error: true, text: error.message }); }
        finally { setLoading(false); }
      }
      React.useEffect(() => { load(); }, []);
      function change(name, value) { setConfig((current) => ({ ...current, [name]: value })); setDirty(true); setNotice(null); setResult(null); }
      async function setMode(next) {
        if (!config || busy || switching || config.mode === next) return;
        const previous = config.mode;
        setConfig({ ...config, mode: next }); setSwitching(true); setNotice(null);
        try {
          const data = await request('POST', { mode: next });
          setConfig(data.config);
          setNotice({ error: false, text: 'OCR 模式已更新。' });
        } catch (error) {
          setConfig({ ...config, mode: previous });
          setNotice({ error: true, text: error.message });
        } finally { setSwitching(false); }
      }
      async function perform(action) {
        if (!config || busy) return;
        if (action === 'test' && !source.trim()) { setNotice({ error: true, text: '请先填写用于测试的文件地址。' }); return; }
        setBusy(action); setNotice(null); setResult(null);
        const payload = { mode: config.mode || 'auto', enabled: (config.mode || 'auto') !== 'off', endpoint: config.endpoint, model: config.model, timeout_seconds: Number(config.timeout_seconds), poll_interval_seconds: Number(config.poll_interval_seconds),
          useDocOrientationClassify: !!config.useDocOrientationClassify, useDocUnwarping: !!config.useDocUnwarping, useChartRecognition: !!config.useChartRecognition };
        if (token) payload.token = token;
        if (action === 'clear') { delete payload.token; payload.clear_token = true; }
        if (action === 'test') payload[sourceType === 'url' ? 'file_url' : 'file_path'] = source.trim();
        try {
          const data = await request('POST', payload, action === 'test');
          if (action === 'test') { setResult(data); setNotice({ error: false, text: data.message || '识别完成，飞桨 OCR 接口可用。' }); }
          else { setConfig(data.config); setToken(''); setDirty(false); setNotice({ error: false, text: action === 'clear' ? '已清除 OCR 密钥。' : 'OCR 设置已保存。' }); }
        } catch (error) { setNotice({ error: true, text: error.message }); }
        finally { setBusy(''); }
      }
      const field = (name, label, options) => h('label', { style: { display: 'grid', gap: 6, marginBottom: 16 } },
        h('span', { style: T.fieldLabel }, label),
        h('input', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, value: config?.[name] ?? '', disabled: !!busy, onChange: (event) => change(name, event.target.value), ...options }));
      return h('section', { 'aria-labelledby': 'wf-ocr-heading', style: { minWidth: 0 } },
        h('h2', { id: 'wf-ocr-heading', style: T.cardTitle }, 'OCR 识别'),
        !config ? h('div', { role: 'status', style: T.hint }, loading ? '正在读取 OCR 设置…' : h('button', { type: 'button', className: 'wf-btn', style: T.btn, onClick: load }, '重新加载设置')) : h('div', null,
        h('div', { style: { ...T.card, boxShadow: 'none' } },
          h('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' } },
            h('h3', { style: { ...T.cardTitle, margin: 0 } }, 'OCR 复核策略'),
            h('div', { role: 'radiogroup', 'aria-label': 'OCR 复核策略', style: { display: 'flex', gap: 6, flexWrap: 'wrap' } },
              [['auto', '自动（AI 判断）'], ['on', '始终开启'], ['off', '关闭']].map(([value, label]) =>
                h('button', { key: value, type: 'button', role: 'radio', 'aria-checked': (config.mode || 'auto') === value,
                  className: 'wf-btn', disabled: !!busy || switching, onClick: () => setMode(value),
                  style: { ...T.btn, ...(((config.mode || 'auto') === value) ? { outline: '2px solid var(--dsw-alias-brand-primary)', fontWeight: 600 } : {}) } }, label)))),
          (config.mode || 'auto') !== 'off' && !config.has_token ? h('div', { style: { ...T.statusErr, marginTop: 10 } }, '尚未保存 Token；需要 OCR 复核时会因缺少凭据失败。') : null),
        h('div', { className: 'wf-settings-card', style: { ...T.card, boxShadow: 'none' } },
          h('h3', null, '接口配置'),
          field('model', '识别模型', { type: 'text', placeholder: 'PaddleOCR-VL-1.6', spellCheck: false }),
          h('label', { style: { display: 'grid', gap: 6, marginBottom: 16 } },
            h('span', { style: T.fieldLabel }, '访问令牌 Token'),
            h('input', { className: 'wf-input', style: { ...T.input, width: '100%' }, type: 'password', autoComplete: 'new-password', spellCheck: false, value: token, disabled: !!busy, placeholder: config.has_token ? '已保存；留空保持不变' : '填写飞桨 API Token', onChange: (event) => { setToken(event.target.value); setDirty(true); setNotice(null); } })),
          h('div', { style: { display: 'grid', gap: 12, marginBottom: 18 } },
            [['useDocOrientationClassify', '自动判断文档方向'], ['useDocUnwarping', '文档去扭曲'], ['useChartRecognition', '图表识别']].map(([name, label]) =>
              h('label', { key: name, style: { display: 'flex', gap: 8, alignItems: 'center' } }, h('input', { type: 'checkbox', checked: !!config[name], disabled: !!busy, onChange: (event) => change(name, event.target.checked) }), h('span', { style: T.fieldLabel }, label)))),
          h('details', { style: { marginBottom: 18 } },
            h('summary', { style: { ...T.hint, cursor: 'pointer', padding: '4px 0', marginBottom: 12 } }, '高级接口参数'),
            field('endpoint', '任务接口地址', { type: 'url', placeholder: 'https://paddleocr.aistudio-app.com/api/v2/ocr/jobs', spellCheck: false, autoComplete: 'off' }),
            field('timeout_seconds', '识别任务超时（秒）', { type: 'number', min: 1, max: 1800, step: 1 }),
            field('poll_interval_seconds', '状态查询间隔（秒）', { type: 'number', min: 0.01, max: 60, step: 0.01 })),
          h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' } },
            h('button', { type: 'button', className: 'wf-btn-primary', style: T.btnPrimary, disabled: !!busy, onClick: () => perform('save') }, busy === 'save' ? '正在保存…' : '保存 OCR 设置'),
            config.has_token ? h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: () => perform('clear') }, busy === 'clear' ? '正在清除…' : '清除密钥') : null),
          h('div', { style: { borderTop: '1px solid var(--dsw-alias-border-l1)', marginTop: 24, paddingTop: 20 } },
            h('h3', null, '识别测试'),
            h('label', { style: { display: 'grid', gap: 6, marginBottom: 12 } },
              h('span', { style: T.fieldLabel }, '文件来源'),
              h('select', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: sourceType, disabled: !!busy, onChange: (event) => { setSourceType(event.target.value); setSource(''); setResult(null); } },
                h('option', { value: 'url' }, '文件 URL'), h('option', { value: 'local' }, '本机文件路径'))),
            h('label', { style: { display: 'grid', gap: 6, marginBottom: 14 } },
              h('span', { style: T.fieldLabel }, sourceType === 'url' ? '公开可访问的文件 URL' : '插件运行电脑上的文件路径'),
              h('input', { className: 'wf-input', style: { ...T.input, width: '100%' }, type: sourceType === 'url' ? 'url' : 'text', value: source, disabled: !!busy, spellCheck: false, autoComplete: 'off', placeholder: sourceType === 'url' ? 'https://example.com/document.pdf' : '/path/to/document.pdf', onChange: (event) => setSource(event.target.value) })),
            h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy || !source.trim() || !(token || config.has_token), onClick: () => perform('test') }, busy === 'test' ? '识别中，正在等待飞桨返回…' : '测试 OCR 识别'),
            busy === 'test' ? h('div', { role: 'status', style: { ...T.hint, marginTop: 10 } }, '任务已发起；排队与识别可能需要一些时间，切换设置分类后仍会保留结果。') : null))),
        notice ? h('div', { role: notice.error ? 'alert' : 'status', 'aria-live': 'polite', style: { ...(notice.error ? T.statusErr : T.statusOk), marginTop: 12, overflowWrap: 'anywhere' } }, notice.text) : null,
        result ? h('div', { style: { marginTop: 16 } },
          h('h3', null, '识别结果'),
          h('div', { style: { ...T.hint, margin: '0 0 8px' } }, ['识别完成', result.pages ? result.pages + ' 页' : '', result.seconds ? result.seconds + ' 秒' : ''].filter(Boolean).join(' · ')),
          h('pre', { className: 'wf-scroll', style: { ...T.input, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 360, overflow: 'auto', fontFamily: 'inherit' } }, result.markdown || result.text || '识别任务已完成。')) : null);
    }

    // 各 ASR 模型的真实下载体积：faster-whisper 转换版 model.bin 的实际字节数（十进制 MB）。
    // 体积不是装饰——它决定下载时长、内存与精度；仅英语模型会把中文题号播报转成拼音。
    const ASR_MODELS = [
      { id: 'large-v3-turbo', mb: 1618, lang: '多语种大模型', tag: '默认', en: false,
        note: 'turbo 解码，约 large-v3 的 6–8 倍速；听力播报、中英混读都稳，切段首选' },
      { id: 'large-v3', mb: 3087, lang: '多语种大模型', tag: '', en: false,
        note: '精度最高档，最慢；turbo 出现漏词/错词时用它复算' },
      { id: 'large-v2', mb: 3087, lang: '多语种大模型', tag: '', en: false,
        note: '与 v3 同体积，中文略弱；只在已有缓存时考虑' },
      { id: 'large-v1', mb: 3087, lang: '多语种大模型', tag: '', en: false,
        note: '老版权重，没有理由优先选' },
      { id: 'large', mb: 3087, lang: '多语种大模型', tag: '= large-v3', en: false,
        note: '别名，实际加载 large-v3，二者等价' },
      { id: 'medium', mb: 1528, lang: '多语种', tag: '', en: false,
        note: '约 large 一半体积，精度明显低于 large 档；内存紧张时的折中' },
      { id: 'small', mb: 484, lang: '多语种小模型', tag: '', en: false,
        note: '快，但会漏词、中文题号识别差；必须明确接受精度损失才选' },
      { id: 'base', mb: 145, lang: '多语种小模型', tag: '', en: false,
        note: '只适合验证链路能否跑通' },
      { id: 'tiny', mb: 75, lang: '多语种小模型', tag: '', en: false,
        note: '最小，仅冒烟测试' },
      { id: 'medium.en', mb: 1528, lang: '仅英语', tag: '', en: true,
        note: '英语专用 medium，中文播报会输出乱码' },
      { id: 'small.en', mb: 484, lang: '仅英语', tag: '', en: true,
        note: '英语专用 small；实测把中文播报转成拼音，不适合本流程' },
      { id: 'base.en', mb: 145, lang: '仅英语', tag: '', en: true,
        note: '英语专用 base，仅冒烟测试' },
      { id: 'tiny.en', mb: 75, lang: '仅英语', tag: '', en: true,
        note: '英语专用 tiny，仅冒烟测试' },
    ];
    const asrLabel = (m) => m.id + '（' + [m.tag, m.lang, '≈' + m.mb + ' MB'].filter(Boolean).join(' · ') + '）';

    // 英语听力切段（WhisperX 可选能力）：适配器随插件分发，运行时/模型懒下载。
    function AudioSettings({ visible } = {}) {
      const React = require('react');
      const h = React.createElement;
      const [info, setInfo] = React.useState(null);
      const [status, setStatus] = React.useState(null);
      const [batches, setBatches] = React.useState([]);
      const [batch, setBatch] = React.useState('');
      const [draft, setDraft] = React.useState(null);
      const [busy, setBusy] = React.useState('');
      const [notice, setNotice] = React.useState(null);
      const [error, setError] = React.useState('');

      async function refreshInfo() {
        const cfg = await (await window.fetch('/wf-plugin/api/audio/config', { headers: { accept: 'application/json' } })).json();
        if (cfg.error) throw new Error(cfg.error);
        setInfo(cfg); setDraft((d) => d || cfg.config);
        return cfg;
      }
      async function refreshBatches() {
        const list = await (await window.fetch('/wf-plugin/api/batches', { headers: { accept: 'application/json' } })).json();
        const names = (list.batches || []).filter((b) => b.has_audio).map((b) => b.name);
        setBatches(names);
        setBatch((current) => (names.includes(current) ? current : ''));
        return names;
      }
      async function refreshStatus(name = batch) {
        if (!name) {
          // 没有选中批次时不读全局状态：全局状态可能属于别的试卷。
          const idle = { batch: '', job: '', running: false, busy: false, phase: 'idle', pct: 0, detail: '', error: '' };
          setStatus(idle); return idle;
        }
        const query = '?batch=' + encodeURIComponent(name);
        const st = await (await window.fetch('/wf-plugin/api/audio/status' + query, { headers: { accept: 'application/json' } })).json();
        setStatus(st);
        return st;
      }
      React.useEffect(() => {
        let active = true;
        (async () => {
          try {
            await refreshInfo();
            if (active) await refreshBatches();
          } catch (e) { if (active) setError(String(e.message || e)); }
        })();
        return () => { active = false; };
      }, []);
      // 设置页是常驻组件：批次列表和状态只在进入「听力切分」时刷新，
      // 避免新上传的批次看不到，也避免把上一个批次的状态当成当前批次。
      React.useEffect(() => {
        if (!visible) return;
        let active = true;
        (async () => {
          try {
            await refreshInfo();
            if (!active) return;
            const names = await refreshBatches();
            if (active) await refreshStatus(names.includes(batch) ? batch : '');
          } catch (e) { if (active) setError(String(e.message || e)); }
        })();
        return () => { active = false; };
      }, [visible]);
      React.useEffect(() => {
        if (!visible) return;
        refreshStatus(batch).catch(() => {});
      }, [batch]);
      React.useEffect(() => {
        if (!status?.busy && !status?.running) return;
        const timer = setInterval(() => { refreshStatus(batch).catch(() => {}); }, 2000);
        return () => clearInterval(timer);
      }, [status?.busy, status?.running, batch]);

      function change(name, value) { setDraft((d) => ({ ...d, [name]: value })); setNotice(null); }
      async function post(path, payload) {
        const r = await window.fetch('/wf-plugin/api/audio/' + path, {
          method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json' },
          body: JSON.stringify(payload || {}),
        });
        const data = await r.json();
        if (!r.ok || data.error) throw new Error(data.error || '请求失败');
        return data;
      }
      async function save() {
        setBusy('save'); setError('');
        try { const d = await post('config', { config: draft }); setDraft(d.config); setNotice('配置已保存。'); await refreshInfo(); }
        catch (e) { setError(String(e.message || e)); } finally { setBusy(''); }
      }
      async function install() {
        setBusy('install'); setError(''); setNotice('开始安装；首次会下载运行时与模型，可能需要几分钟。');
        try { await post('bootstrap', { force: false }); await refreshInfo(); }
        catch (e) { setError(String(e.message || e)); } finally { setBusy(''); }
      }
      async function segment() {
        if (!batch) { setError('请先选择批次。'); return; }
        setBusy('segment'); setError('');
        try { await post('segment', { name: batch }); setNotice('已启动「' + batch + '」的切段任务。'); await refreshStatus(batch); }
        catch (e) { setError(String(e.message || e)); } finally { setBusy(''); }
      }
      const field = (name, label, extra, help) => h('label', { style: { display: 'grid', gap: 6, marginBottom: 14 } },
        h('span', { style: T.fieldLabel }, label),
        h('input', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, value: draft?.[name] ?? '', disabled: !!busy, onChange: (e) => change(name, e.target.value), ...(extra || {}) }),
        help ? h('span', { style: T.hint }, help) : null);
      const running = !!(status?.busy || status?.running);
      return h('section', { 'aria-labelledby': 'wf-audio-heading', style: { minWidth: 0 } },
        h('h2', { id: 'wf-audio-heading', style: T.cardTitle }, '听力切分'),
        h('p', { className: 'wf-settings-description' }, '配置转写模型与切分参数，为已确认结构的试卷生成听力片段。'),
        h('div', { style: { ...T.card, marginBottom: 0, boxShadow: 'none' } },
          h('div', { style: T.hint }, '结构闸门确认后会自动切段；也可在这里选择批次手动重跑。WhisperX 词级时间戳只用来找题号锚点，切点由 ffmpeg 长静音骨架决定。'),
          !info || !draft ? h('div', { style: { ...T.hint, marginTop: 10 } }, '正在读取设置…') : h('div', { style: { marginTop: 12 } },
            h('label', { style: { display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14 } },
              h('input', { type: 'checkbox', checked: !!draft.enabled, disabled: !!busy, onChange: (e) => change('enabled', e.target.checked) }),
              h('span', { style: T.fieldLabel }, '启用听力切段适配器')),
            h('label', { style: { display: 'grid', gap: 6, marginBottom: 14 } },
              h('span', { style: T.fieldLabel }, 'ASR 模型'),
              h('select', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: draft.asr_model, disabled: !!busy, onChange: (e) => change('asr_model', e.target.value) },
                [false, true].map((isEn) => h('optgroup', { key: isEn ? 'en' : 'multi', label: isEn ? '仅英语（中文播报会转成拼音，不建议）' : '多语种（推荐）' },
                  ASR_MODELS.filter((m) => m.en === isEn).map((m) => h('option', { key: m.id, value: m.id }, asrLabel(m)))))),
              h('span', { style: T.hint }, '体积是 model.bin 的下载体积（十进制 MB，约数），不含 venv 的 4–6 GB。默认多语种大模型；安装失败不会自动换成小模型。英文词级对齐使用 facebook/wav2vec2-base-960h。')),
            h('details', { style: { margin: '0 0 16px' } },
              h('summary', { style: { cursor: 'pointer', ...T.hint, padding: '4px 0', marginBottom: 10 } }, '各模型体积与特点'),
              h('div', { style: { display: 'grid', gap: 6 } },
                h('div', { style: { display: 'grid', gridTemplateColumns: 'minmax(110px, 150px) minmax(64px, 84px) minmax(0, 1fr)', gap: 8, ...T.hint } },
                  h('span', null, '模型'), h('span', null, '下载体积'), h('span', null, '特点')),
                ASR_MODELS.map((m) => h('div', { key: m.id, style: { display: 'grid', gridTemplateColumns: 'minmax(110px, 150px) minmax(64px, 84px) minmax(0, 1fr)', gap: 8, alignItems: 'baseline', borderTop: '1px solid var(--dsw-alias-border-l1)', paddingTop: 6 } },
                  h('span', { style: T.fieldLabel }, m.id + (m.tag ? '（' + m.tag + '）' : '')),
                  h('span', { style: T.hint }, '≈' + m.mb + ' MB'),
                  h('span', { style: T.hint }, m.lang + '：' + m.note)))),
              h('div', { style: { ...T.hint, marginTop: 8 } }, '体积按 faster-whisper 转换版 model.bin 计，不同镜像或量化方式可能差几 MB。')),
            field('skeleton_silence', '骨架静音阈值（秒）', { type: 'number', min: 0.5, max: 20, step: 0.5 }, '≥3s 的长静音中点作为切点骨架。'),
            h('details', { style: { margin: '4px 0 18px' } },
              h('summary', { style: { cursor: 'pointer', ...T.hint, padding: '4px 0', marginBottom: 12 } }, '高级参数'),
              field('cut_silence', '静音探测最短时长（秒）', { type: 'number', min: 0.1, max: 5, step: 0.05 }),
              field('noise', '静音判定阈值', { type: 'text', placeholder: '-35dB' }),
              field('anchor_language', '播报语言（留空关闭辅助转写）', { type: 'text', placeholder: 'zh' }),
              field('cpu_threads', '每个转写任务的 CPU 线程数', { type: 'number', min: 1, max: 64, step: 1 }),
              field('batch_size', '转写批量大小', { type: 'number', min: 1, max: 64, step: 1 }),
              field('max_parallel_transcriptions', '同时驻留的转写模型数', { type: 'number', min: 1, max: 2, step: 1 },
                '大模型默认 1，避免两份模型争抢内存；资源充足时可设为 2。'),
              field('hf_endpoint', '模型下载镜像（留空自动探测）', { type: 'text', placeholder: 'https://hf-mirror.com' },
                'huggingface.co 不可达时自动用 hf-mirror.com；也可填公司内网镜像。'),
              h('label', { style: { display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14 } },
                h('input', { type: 'checkbox', checked: !!draft.cpu_torch, disabled: !!busy,
                  onChange: (e) => change('cpu_torch', e.target.checked) }),
                h('span', { style: T.fieldLabel }, 'Linux 上优先装 CPU 版 torch（避免数 GB 的 CUDA 依赖）')),
              field('venv', '复用已有 venv（留空则自建）', { type: 'text', placeholder: '例如 ~/.local/share/video-kit/venv' },
                '填已存在的虚拟环境目录；仍会自动补齐缺失依赖。'),
              field('data_dir', '运行时/模型目录（留空用数据根）', { type: 'text', placeholder: '~/.dsh/wf-plugin-data/audio' })),
            h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' } },
              h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: save }, busy === 'save' ? '正在保存…' : '保存设置'),
              h('button', { type: 'button', className: 'wf-btn-primary', style: T.btnPrimary, disabled: !!busy || running, onClick: install },
                info.ready ? '重新检查 / 强制更新' : '安装运行时与模型'),
              h('span', { style: T.hint }, info.ready ? '✓ 已就绪' : '未安装')),
            h('div', { style: { borderTop: '1px solid var(--dsw-alias-border-l1)', marginTop: 18, paddingTop: 16 } },
              h('label', { style: { display: 'grid', gap: 6, marginBottom: 12 } },
                h('span', { style: T.fieldLabel }, '为批次生成听力切段'),
                h('div', { style: { display: 'flex', gap: 8, alignItems: 'center', minWidth: 0 } },
                  h('select', { className: 'wf-input', style: { ...T.input, flex: 1, minWidth: 0 }, value: batch, disabled: !!busy, onChange: (e) => setBatch(e.target.value) },
                    h('option', { value: '' }, '选择批次…'),
                    batches.map((n) => h('option', { key: n, value: n }, n))),
                  h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: () => refreshBatches().catch((e) => setError(String(e.message || e))) }, '刷新批次')),
                h('span', { style: T.hint }, '列表只显示已上传整卷听力音频的批次；新上传后点「刷新批次」或重新进入此页即可看到。')),
              h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy || running || !info.ready || !batch, onClick: segment },
                busy === 'segment' ? '正在切段…' : '生成切段'),
              !info.ready ? h('span', { style: { ...T.hint, marginLeft: 8 } }, '请先安装运行时。') : null),
            info.ffmpeg ? null : h('div', { role: 'alert', style: { ...T.statusErr, marginTop: 12 } }, '未检测到 ffmpeg/ffprobe：插件 vendor/ 里没有本平台二进制，系统 PATH 也找不到。请联系打包者运行 fetch_vendor.py，或自行安装 ffmpeg。'),
            h('div', { style: { ...T.hint, marginTop: 12 } }, '数据目录：' + info.data_dir),
            h('div', { style: T.hint }, '模型镜像：' + (info.hf_endpoint || '直连 huggingface.co')),
          ),
          status && batch ? h('div', { style: { borderTop: '1px solid var(--dsw-alias-border-l1)', marginTop: 16, paddingTop: 14, display: 'grid', gap: 6 } },
            h('div', { style: T.fieldLabel }, '任务状态：' + (status.phase || 'idle') + (status.pct ? ' · ' + status.pct + '%' : '') + (status.batch ? ' · ' + status.batch : '')),
            status.detail ? h('div', { style: T.hint }, status.detail) : null,
            status.error ? h('div', { role: 'alert', style: T.statusErr }, status.error) : null) : null,
          notice ? h('div', { role: 'status', style: { ...T.statusOk, marginTop: 12 } }, notice) : null,
          error ? h('div', { role: 'alert', style: { ...T.statusErr, marginTop: 12, overflowWrap: 'anywhere' } }, error) : null));
    }

    function SettingsTab() {
      const React = require('react');
      const h = React.createElement;
      // 默认停在「初始化引导」：新装用户第一眼就看到要装/要配什么。
      const [activeSection, setActiveSection] = React.useState('onboarding');
      const [visited, setVisited] = React.useState({ onboarding: true });
      const sections = [['onboarding', '初始化引导'], ['models', '模型设置'], ['api', '上传设置'], ['ocr', 'OCR 识别'], ['audio', '听力切分']];
      function navigate(id) {
        setVisited((current) => ({ ...current, [id]: true })); setActiveSection(id);
      }
      function onNavKey(event, index) {
        const delta = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0;
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? sections.length - 1 : delta ? (index + delta + sections.length) % sections.length : -1;
        if (next < 0) return;
        event.preventDefault(); navigate(sections[next][0]);
        event.currentTarget.parentElement.querySelectorAll('[role=tab]')[next]?.focus();
      }
      function navIcon(id) {
        const paths = { onboarding: 'M9 2l2.2 4.6L16 7.3l-3.5 3.4.8 4.8L9 13.2 4.7 15.5l.8-4.8L2 7.3l4.8-.7z', models: 'M4 3h10v12H4z M7 6h4 M7 9h4 M7 12h2', api: 'M6.5 5L3 9l3.5 4 M11.5 5L15 9l-3.5 4 M10 3L8 15', ocr: 'M6 3H3v3 M12 3h3v3 M15 12v3h-3 M6 15H3v-3 M6 6h6v6H6z', audio: 'M3 7v4 M6 4v10 M9 2v14 M12 5v8 M15 7v4' };
        return h('svg', { width: 16, height: 16, viewBox: '0 0 18 18', fill: 'none', stroke: 'currentColor', strokeWidth: 1.4, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }, h('path', { d: paths[id] }));
      }
      const [models, setModels] = React.useState([]);
      const [router, setRouter] = React.useState(null);
      const [status, setStatus] = React.useState('idle');
      const [err, setErr] = React.useState('');

      React.useEffect(() => {
        (async () => {
          try {
            const m = await (await window.fetch('/wf-plugin/models', { headers: { accept: 'application/json' } })).json();
            setModels(m.models || []);
            const cfg = await (await window.fetch('/wf-plugin/config', { headers: { accept: 'application/json' } })).json();
            setRouter(cfg.router || {});
          } catch (e) { setErr(String(e.message || e)); }
        })();
      }, []);

      const byKey = React.useMemo(() => { const map = {}; for (const m of models) map[modelKey(m)] = m; return map; }, [models]);
      const defaultSel = router?.default?.model ? router.default : (router?.harnessDefault || null);

      async function persist(next) {
        const previous = router;
        setRouter(next); setStatus('saving'); setErr('');
        try {
          const r = await window.fetch('/wf-plugin/router', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(next) });
          if (!r.ok) throw new Error(await r.text());
          const res = await r.json(); setRouter(res.router); setStatus('saved');
          setTimeout(() => setStatus((s) => s === 'saved' ? 'idle' : s), 1500);
        } catch (e) { setRouter(previous); setErr(String(e.message || e)); setStatus('idle'); }
      }
      const inferProfile = () => 'wf-deepseek-fast';
      const modelId = (entry) => (entry && entry.provider && entry.model ? entry.provider + '|' + entry.model : '');
      const isPlainMap = (value) => !!value && typeof value === 'object' && !Array.isArray(value);
      // 换全局默认时，凡是仍沿用旧默认值的子 Agent / 审核轮次也必须一起跟随，
      // 否则它们会保留旧模型，界面上看起来“下面的没跟着改”。
      // 损坏配置（数组/字符串等非对象）保持原样、不在此处改写，交由读取处的 || {} 兜底。
      function setDefault(sel) {
        const previous = router?.default || {};
        const changed = modelId(previous) !== modelId(sel);
        const next = { ...router, default: { ...previous, ...sel, profile: inferProfile() } };
        const stale = (entry) => changed && modelId(entry) === modelId(previous);
        for (const group of ['roles', 'stages']) {
          if (!isPlainMap(next[group])) continue;
          const map = { ...next[group] };
          for (const key of Object.keys(map)) if (stale(map[key])) delete map[key];
          next[group] = map;
        }
        if (isPlainMap(next.review) && isPlainMap(next.review.rounds)) {
          const rounds = { ...next.review.rounds };
          for (const key of Object.keys(rounds)) if (stale(rounds[key])) delete rounds[key];
          next.review = { ...next.review, rounds };
        }
        persist(next);
      }
      function setStage(stage, sel) { const roles = { ...(router?.roles || {}) }; roles[stage] = { ...sel, profile: inferProfile() }; persist({ ...router, roles }); }
      function clearStage(stage) { const roles = { ...(router?.roles || {}) }; delete roles[stage]; persist({ ...router, roles }); }
      function setRound(round, sel) { const review = { ...(router?.review || {}) }; const rounds = { ...(review.rounds || {}) }; rounds[round] = { ...sel, profile: inferProfile() }; review.rounds = rounds; persist({ ...router, review }); }
      function clearRound(round) { const review = { ...(router?.review || {}) }; const rounds = { ...(review.rounds || {}) }; delete rounds[round]; review.rounds = rounds; persist({ ...router, review }); }

      function ModelSelect({ value, onChange, onClear }) {
        const hasOwn = value && value.provider && value.model;
        const v = hasOwn ? modelKey(value) : '';
        const follow = defaultSel ? ('跟随默认（' + (byKey[modelKey(defaultSel)]?.label || defaultSel.model) + '）') : '跟随默认模型';
        return h('select', {
          className: 'wf-input', style: { ...T.input, width: '100%' }, value: v, disabled: !router || status === 'saving' || status === 'testing',
          onChange: (e) => { if (e.target.value === '') { onClear && onClear(); return; } const m = byKey[e.target.value]; if (m) onChange({ provider: m.provider, model: m.model }); },
        },
          h('option', { value: '' }, follow),
          models.map((m) => h('option', { key: modelKey(m), value: modelKey(m) }, m.label + ' · ' + m.provider)));
      }

      const stages = [['answers', '答案提取'], ['audio', '听力切分'], ['types', '题型判定'], ['explanations', '解析与大意'], ['knowledge', '知识点标注'], ['assemble', '填充汇总'], ['review', '审核'], ['operations', 'AI运维']];
      const supplementRoles = [['ai_summary', '文章大意'], ['ai_explanation', '答案解析']];
      const rounds = [['labels', '一审']];

      async function writeEffective() {
        setStatus('testing'); setErr('');
        try {
          const response = await window.fetch('/wf-plugin/materialize', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
          const data = await response.json();
          if (!response.ok || data.ok === false || data.error) throw new Error(data.error || '写入生效配置失败，请重试。');
          setStatus('tested');
        } catch (e) { setErr(String(e.message || e)); setStatus('idle'); }
      }

      return h('div', { className: 'wf-settings-page' },
        h('div', { className: 'wf-settings-layout' },
          h('nav', { className: 'wf-settings-nav wf-scroll', role: 'tablist', 'aria-label': '设置分类', 'aria-orientation': 'vertical' },
            h('p', { className: 'wf-settings-nav-title' }, '设置'),
            sections.map(([id, label], index) => h('button', { key: id, type: 'button', id: 'wf-settings-tab-' + id, role: 'tab', 'aria-selected': activeSection === id, 'aria-controls': 'wf-settings-panel-' + id, tabIndex: activeSection === id ? 0 : -1, onClick: () => navigate(id), onKeyDown: (event) => onNavKey(event, index) }, navIcon(id), label))),
          h('div', { className: 'wf-settings-content wf-scroll' },
          h('div', { className: 'wf-settings-section', role: 'tabpanel', id: 'wf-settings-panel-onboarding', 'aria-labelledby': 'wf-settings-tab-onboarding', hidden: activeSection !== 'onboarding', tabIndex: 0 },
            visited.onboarding ? h(OnboardingSection, { onNavigate: navigate }) : null),
          h('div', { className: 'wf-settings-section', role: 'tabpanel', id: 'wf-settings-panel-models', 'aria-labelledby': 'wf-settings-tab-models', hidden: activeSection !== 'models', tabIndex: 0 },
          h('section', { 'aria-labelledby': 'wf-model-settings-heading', style: { minWidth: 0 } },
          h('h2', { id: 'wf-model-settings-heading', style: T.cardTitle }, '模型设置'),
          h('p', { className: 'wf-settings-description' }, '默认跟随本机 Harness 当前模型，无需填写 API Key；也可显式指定全局默认、各处理阶段、文章大意/答案解析与一审审核模型。改动自动保存。'),
          err ? h('div', { role: 'alert', style: { ...T.statusErr, marginBottom: 8 } }, err) : null,
          h('div', { style: { ...T.card, boxShadow: 'none' } },
            h('h3', { style: T.cardTitle }, TEXT.defaultModel),
            models.length === 0 ? h('div', { style: T.hint }, '正在读取模型…') :
              h('div', { style: { display: 'grid', gap: 8, minWidth: 0 } },
                h('select', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, disabled: !router || status === 'saving' || status === 'testing', value: (router?.default?.model ? modelKey(router.default) : (router?.harnessDefault ? modelKey(router.harnessDefault) : '')), onChange: (e) => { const m = byKey[e.target.value]; if (m) setDefault({ provider: m.provider, model: m.model }); } },
                  models.map((m) => h('option', { key: modelKey(m), value: modelKey(m) }, m.label + ' · ' + m.provider))),
                h('span', { style: T.hint }, '当前：' + (router?.default?.model ? '（插件默认）' : '（跟随 Harness）') + (byKey[router?.default?.model ? modelKey(router.default) : (router?.harnessDefault ? modelKey(router.harnessDefault) : '')]?.label || router?.default?.model || router?.harnessDefault?.model || '—')),
        h('div', { style: { ...T.hint, marginTop: 6 } }, '修改全局默认时，仍跟随默认的子 Agent 与审核轮次会一起切换；已单独指定的保持不变。')),
          ),
          h('div', { style: { ...T.card, boxShadow: 'none' } },
            h('h3', { style: T.cardTitle }, TEXT.stageModels),
            h('div', { style: { ...T.hint, marginBottom: 10 } }, '内置 8 个子 Agent：前置组装题型判定、答案提取、听力切段、题型兼容校验、解析抄录、知识点标注、装配、审核；AI运维为附加能力，可为它单独选模型（未选时跟随默认/Harness 当前模型）。默认全部「跟随默认」，需要单独指定时才选。'),
            h('div', { className: 'wf-model-fields', style: { display: 'grid', gridTemplateColumns: '120px minmax(0,1fr)', gap: '10px 14px', alignItems: 'center' } },
              stages.map(([s, lbl]) => h('div', { key: s, style: { display: 'contents' } },
                h('span', { style: T.label }, lbl),
                h(ModelSelect, { value: router?.roles?.[s] || router?.stages?.[s] || null, onChange: (sel) => setStage(s, sel), onClear: () => clearStage(s) })))),
          ),
          h('div', { style: { ...T.card, boxShadow: 'none' } },
            h('h3', { style: T.cardTitle }, TEXT.aiSupplementModels),
            h('div', { style: { ...T.hint, marginBottom: 10 } }, '用于工作台手动点击「AI文章大意」与「AI解析」的独立生成任务，不与试卷上传流程冲突。默认跟随全局默认模型，也可分别指定更快的模型。'),
            h('div', { className: 'wf-model-fields', style: { display: 'grid', gridTemplateColumns: '120px minmax(0,1fr)', gap: '10px 14px', alignItems: 'center' } },
              supplementRoles.map(([s, lbl]) => h('div', { key: s, style: { display: 'contents' } },
                h('span', { style: T.label }, lbl),
                h(ModelSelect, { value: router?.roles?.[s] || router?.stages?.ai_supplement || null, onChange: (sel) => setStage(s, sel), onClear: () => clearStage(s) })))),
          ),
          h('div', { style: { ...T.card, boxShadow: 'none' } },
            h('h3', { style: T.cardTitle }, TEXT.reviewModels),
            h('div', { style: { ...T.hint, marginBottom: 10 } }, '默认「跟随默认」；可单独指定一审模型（已取消二审抽审，改为自动「标签合并」）。'),
            h('div', { className: 'wf-model-fields', style: { display: 'grid', gridTemplateColumns: '120px minmax(0,1fr)', gap: '10px 14px', alignItems: 'center' } },
              rounds.map(([r, lbl]) => h('div', { key: r, style: { display: 'contents' } },
                h('span', { style: T.label }, lbl),
                h(ModelSelect, { value: router?.review?.rounds?.[r] || null, onChange: (sel) => setRound(r, sel), onClear: () => clearRound(r) })))),
            h('div', { style: { marginTop: 14, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' } },
              h('button', { className: 'wf-btn', style: T.btn, disabled: status === 'saving' || status === 'testing' || !router, onClick: writeEffective }, status === 'testing' ? '正在写入…' : TEXT.writeEffective),
              h(BackendRestartControl, {}),
              status === 'saving' ? h('span', { style: T.hint }, '正在保存…')
                : status === 'saved' ? h('span', { style: T.statusOk }, '✓ 已保存，下次进入会记住')
                : status === 'tested' ? h('span', { style: T.statusOk }, '✓ 已写入生效配置')
                : h('span', { style: T.hint }, '改动会自动保存')),
          )),
          ),
          [['api', QuestionBankSettings], ['ocr', OCRSettings], ['audio', AudioSettings]].map(([id, Component]) =>
            h('div', { key: id, className: 'wf-settings-section', role: 'tabpanel', id: 'wf-settings-panel-' + id, 'aria-labelledby': 'wf-settings-tab-' + id, hidden: activeSection !== id, tabIndex: 0 }, visited[id] ? h(Component, id === 'audio' ? { visible: activeSection === id } : null) : null)),
          ),
        ),
      );
    }

    // ======================================================================
    // 面板外壳：三个标签页
    // ======================================================================
    function WfPanel(props) {
      const React = require('react');
      const h = React.createElement;
      const [tab, setTab] = React.useState('upload');
      const [uploadedTick, setUploadedTick] = React.useState(0);
      const [visited, setVisited] = React.useState({ upload: true, review: false, settings: false });
      const switchTab = (id) => {
        setTab(id);
        setVisited((v) => (v[id] ? v : { ...v, [id]: true }));
      };
      const tabBtn = (id, label) => h('button', { key: id, onClick: () => switchTab(id), style: T.tab(tab === id), 'aria-current': tab === id ? 'page' : undefined }, label);
      const paneStyle = (id) => ({ display: tab === id ? 'flex' : 'none', flex: 1, minWidth: 0, minHeight: 0, flexDirection: 'column' });
      return h('div', { className: 'wf-root', style: T.root },
        // Harness turns this header into a native drag region and excludes its buttons.
        h('div', { style: T.tabbar, 'data-window-drag': '' },
          h('div', { style: T.brand },
            h('img', { style: T.brandMark, src: BRAND_LOGO, alt: '', draggable: false }),
            h('span', { style: T.brandName }, TEXT.brandTitle)),
          h('div', { style: T.segWrap, role: 'tablist' },
            tabBtn('upload', TEXT.upload),
            tabBtn('review', TEXT.review),
            tabBtn('settings', TEXT.settings)),
          h('span', { style: { flex: 1 } }),
          props.closePanel ? h('button', { className: 'wf-icon-btn', title: TEXT.close, 'aria-label': TEXT.close, onClick: props.closePanel, style: T.iconBtn }, closeIcon()) : null,
        ),
        h('div', { style: T.body },
          h('div', { style: paneStyle('upload') },
            h(UploadView, {
              isActive: tab === 'upload',
              // 上传后留在上传页：继续看新试卷从第一步跑起的流程，不被自动跳走打断。
              onUploaded: () => { setUploadedTick((x) => x + 1); },
              onGoReview: () => { setUploadedTick((x) => x + 1); switchTab('review'); },
            }),
          ),
          visited.review ? h('div', { style: paneStyle('review') },
            h(WorkbenchTab, { isActive: tab === 'review', uploadedTick }),
          ) : null,
          visited.settings ? h('div', { style: paneStyle('settings') },
            h(SettingsTab, {}),
          ) : null,
        ),
      );
    }

    function SidebarIcon() {
      const React = require('react');
      const h = React.createElement;
      return h('span', { title: TEXT.title, 'aria-label': TEXT.title, style: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 20, color: 'var(--dsw-alias-label-secondary)' } }, docIcon(17));
    }

    function apply(ctx) {
      try {
        if (!ctx || typeof ctx.inject !== 'function') { console.error('[wf-plugin] ctx.inject unavailable'); return; }
        try { injectStyles(); } catch (e) { /* 非浏览器环境忽略 */ }
        ctx.inject(['slots'], (scope) => {
          scope.slots.inject('main', () => scope.slots.register({ name: 'main', key: 'wf-plugin', inject: (p) => ({ ...(p || {}), presentation: 'panel' }) }, WfPanel));
          scope.slots.inject('sidebar.panellist', () => scope.slots.register({ name: 'sidebar.panellist', id: 'wf-plugin', order: 41, label: TEXT.title }, SidebarIcon));
        });
      } catch (e) { console.error('[wf-plugin] client apply failed:', e); }
    }

    exports.apply = apply;
    exports.inject = ['slots'];
    exports.__panel = { WfPanel, UploadView, ReviewView, WorkbenchTab, SettingsTab, ContentModal, LabelModal, BackendRestartControl, __cache: { IMG_CACHE, IMG_RESOLVED, UNIT_CACHE, syncCachedImage, collectBlockImageUrls, prefetchImages, getCachedUnit, setCachedUnit, invalidateBatchUnits, loadBatchUnitsBundle, PreviewImage } };
    return module.exports;
  },
});
