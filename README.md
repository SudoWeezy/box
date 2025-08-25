# 📦 On-Chain Variable Storage for Algorand

This repository explores a design pattern for managing **large variable storage** on Algorand smart contracts, beyond the 32 KB per-box limit.  

The approach is inspired by **stack vs. heap memory management** in traditional programming.  

---

## 🔧 How it Works

We introduce two abstractions:  

- **META** → An index that tracks variables, their total size, and how many chunks they are split into.  
- **HEAP** → A storage space where variable data is chunked and stored across multiple boxes (each max 32 KB).  

### Example: Variable `"a"` storing a 100 KB JSON  

1. `"a"` is **hashed** with `sha256` to produce a **base key**.  
2. The value is automatically split into chunks: `a0`, `a1`, `a2`, `a3`.  
3. Each chunk key is derived as:  