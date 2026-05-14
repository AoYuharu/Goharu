import random
import time
from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class Product:
    name: str
    price: float
    stock: int


@dataclass
class Customer:
    name: str
    balance: float
    cart: Dict[str, int] = field(default_factory=dict)

    def add_to_cart(self, product_name: str, quantity: int):
        if product_name not in self.cart:
            self.cart[product_name] = 0
        self.cart[product_name] += quantity

    def clear_cart(self):
        self.cart.clear()


class Inventory:
    def __init__(self):
        self.products: Dict[str, Product] = {}

    def add_product(self, product: Product):
        self.products[product.name] = product

    def show_products(self):
        print("\n=== 商品列表 ===")
        for product in self.products.values():
            print(
                f"{product.name:<10} "
                f"价格: {product.price:<6} "
                f"库存: {product.stock}"
            )

    def has_stock(self, product_name: str, quantity: int) -> bool:
        if product_name not in self.products:
            return False
        return self.products[product_name].stock >= quantity

    def reduce_stock(self, product_name: str, quantity: int):
        if self.has_stock(product_name, quantity):
            self.products[product_name].stock -= quantity


class PaymentGateway:
    def process_payment(self, customer: Customer, amount: float) -> bool:
        print(f"\n正在处理支付: {amount:.2f} 元")
        time.sleep(1)

        if customer.balance >= amount:
            customer.balance -= amount
            print("支付成功")
            return True

        print("余额不足")
        return False


class OrderSystem:
    def __init__(self, inventory: Inventory, gateway: PaymentGateway):
        self.inventory = inventory
        self.gateway = gateway
        self.order_history: List[dict] = []

    def calculate_total(self, customer: Customer) -> float:
        total = 0

        for product_name, quantity in customer.cart.items():
            if product_name in self.inventory.products:
                product = self.inventory.products[product_name]

                subtotal = product.price * quantity
                total += subtotal

                print(
                    f"{product_name:<10} "
                    f"x {quantity:<3} "
                    f"= {subtotal:.2f}"
                )

        return total

    def checkout(self, customer: Customer):
        print(f"\n===== {customer.name} 开始结账 =====")

        if not customer.cart:
            print("购物车为空")
            return

        # 先计算总价
        total = self.calculate_total(customer)
        print(f"\n订单总价: {total:.2f} 元")

        # 检查库存（原子操作：检查并记录需要扣减的数量）
        insufficient = []
        for product_name, quantity in customer.cart.items():
            if not self.inventory.has_stock(product_name, quantity):
                insufficient.append(product_name)
        
        if insufficient:
            for name in insufficient:
                print(f"{name} 库存不足")
            return

        # 处理支付
        success = self.gateway.process_payment(customer, total)

        if success:
            # 支付成功后扣减库存（此时已确认有足够库存）
            for product_name, quantity in customer.cart.items():
                self.inventory.reduce_stock(product_name, quantity)

            order = {
                "customer": customer.name,
                "items": customer.cart,
                "total": total,
                "timestamp": time.time()
            }

            self.order_history.append(order)

            print("\n订单完成")
            print(f"用户剩余余额: {customer.balance:.2f}")

            customer.clear_cart()

    def show_history(self):
        print("\n===== 历史订单 =====")

        if not self.order_history:
            print("暂无订单")
            return

        for idx, order in enumerate(self.order_history, start=1):
            print(f"\n订单 {idx}")
            print(f"用户: {order['customer']}")
            print(f"金额: {order['total']:.2f}")
            print(f"商品:")

            for product_name, quantity in order["items"].items():
                print(f"  - {product_name}: {quantity}")


def generate_random_customer() -> Customer:
    names = ["Alice", "Bob", "Charlie", "David", "Eve"]

    name = random.choice(names)
    balance = random.randint(50, 300)

    return Customer(name=name, balance=balance)


def simulate_shopping(customer: Customer, inventory: Inventory):
    product_names = list(inventory.products.keys())

    for _ in range(random.randint(1, 4)):
        product_name = random.choice(product_names)
        quantity = random.randint(1, 3)

        customer.add_to_cart(product_name, quantity)

    print(f"\n{customer.name} 的购物车:")
    for product_name, quantity in customer.cart.items():
        print(f"  {product_name} x {quantity}")


def main():
    inventory = Inventory()

    inventory.add_product(Product("键盘", 99.0, 10))
    inventory.add_product(Product("鼠标", 45.0, 15))
    inventory.add_product(Product("耳机", 120.0, 8))
    inventory.add_product(Product("显示器", 899.0, 3))
    inventory.add_product(Product("USB线", 15.0, 30))

    gateway = PaymentGateway()

    system = OrderSystem(inventory, gateway)

    inventory.show_products()

    customers = []

    for _ in range(3):
        customer = generate_random_customer()
        customers.append(customer)

    for customer in customers:
        simulate_shopping(customer, inventory)
        system.checkout(customer)

    inventory.show_products()

    system.show_history()


if __name__ == "__main__":
    main()