import hashlib
salt = '6a74369340ad1dedc180'
difficulty = 144000

# Find first answer where int(hash[:4], 16) < difficulty
for i in range(200000):
    h = hashlib.sha3_256(f'{salt}{i}'.encode()).hexdigest()
    if int(h[:4], 16) < difficulty:
        print(f'First valid answer: {i}, hash={h[:16]}')
        break

# Verify known answer 62997 also satisfies
h = hashlib.sha3_256(f'{salt}62997'.encode()).hexdigest()
print(f'Known answer 62997: int(hash[:4])={int(h[:4],16)} < {difficulty}? {int(h[:4],16) < difficulty}')
