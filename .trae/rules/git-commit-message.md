---
alwaysApply: true
scene: git_message
---

## Git Commit Message 规范 (Conventional Commits)

### 格式

```
<type>(<scope>): <subject>

[body]

[footer]
```

- **subject**: 祈使句，首字母小写，不加句号，≤72字符
- **scope** (可选): 模块/文件范围，如 `ui`, `auth`, `api`
- **body** (可选): 解释 what & why（非 how），每行≤72字符
- **footer** (可选): `Closes #123`, `BREAKING CHANGE: ...

### 规则

1. 一个 diff 只写一条 commit，不混不相关改动
2. Subject 统一用中文，保持一致
3. BREAKING CHANGE 在 footer 或 type 后加 `!`：`feat!: 移除旧版API`
4. 生成后检查：message 是否准确反映 diff 内容
5. 保持 message 简洁，避免过长
