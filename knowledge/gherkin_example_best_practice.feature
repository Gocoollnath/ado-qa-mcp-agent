Feature: E-commerce Shopping Cart and Order Processing
  As an online customer
  I want to browse products, manage my shopping cart, and complete purchases with comprehensive order processing
  So that I can efficiently shop while ensuring accurate order fulfillment and system reliability

  Background:
    Given I am a registered customer logged into the e-commerce platform
    And I have access to the product catalog and shopping cart system
    And the inventory management system is fully operational

  @smoke @critical @end-to-end
  Scenario: Successfully complete purchase with valid product selection and payment
    Given I have selected 10 valid products with correct prices and availability
    When I add the products to my shopping cart
    And I review the cart contents for accuracy and total calculations
    And I proceed to checkout
    And I enter valid payment and shipping information
    And I confirm the order
    Then all 10 items should be added to my order successfully
    And I should receive order confirmation with detailed order summary
    And the products should be reserved from inventory immediately
    And I should be able to track the order status in real-time

  @regression @error-recovery @end-to-end
  Scenario: Identify and handle items with inventory and pricing issues during checkout
    Given I have added items to cart containing out-of-stock products, price mismatches, and discontinued items
    When I proceed to the review and checkout stage
    Then the system should identify and flag all problematic items
    When I systematically remove out-of-stock items
    And I address price discrepancies by accepting updated pricing
    And I replace discontinued items with available alternatives
    And I proceed with checkout using corrected items
    Then only valid and available items should be processed in the order
    And I should receive detailed summary of cart modifications
    And the checkout should complete successfully with full order integrity maintained

  @regression @complex-data @end-to-end
  Scenario: Process large mixed cart with diverse products and complex conditions
    Given I have a shopping cart with 50 items containing regular products, promotional items, bundles, edge case quantities, and boundary conditions
    When I review the comprehensive pricing analysis for all items
    And I systematically apply applicable discounts and promotional codes
    And I address any bundled item restrictions and dependencies
    And I make decisions on quantity limits and maximum cart values
    And I finalize the order with all items processed
    Then all eligible products should be successfully added to the order
    And promotional discounts should be accurately applied according to rules
    And pricing should reflect all applicable reductions and fees
    And the system should maintain optimal performance with large cart volume

  @regression @alternative-workflow @end-to-end
  Scenario: Complete checkout using iterative modification and validation approach
    Given I have a shopping cart with various items containing pricing conflicts and inventory concerns
    When I begin the review and modification process
    And I use filtering options to focus on specific issue categories systematically
    And I modify quantities and selections in logical batches
    And I validate inventory availability before proceeding to next batch
    And I complete the checkout after resolving all items through iterative approach
    Then the order should be successfully placed with all modifications applied
    And no items should be lost during the iterative modification process
    And I should have complete confirmation of all changes made to the cart

  @regression @boundary @integration @end-to-end
  Scenario: Handle maximum cart capacity with system performance validation
    Given I have prepared the maximum allowed cart with 100 product items
    When I add the maximum capacity dataset to my shopping cart
    And I navigate through the large cart review interface
    And I manage memory and performance considerations during navigation
    And I complete the checkout process within system constraints
    Then the system should handle the maximum load without performance degradation
    And all items should be processed within acceptable time parameters
    And I should receive confirmation with performance metrics for large order
    And system resources should return to normal after order completion

  @regression @integration @end-to-end
  Scenario: Complete purchase with post-order system integration verification
    Given I have a cart with items requiring various system integrations and fulfillment workflows
    When I successfully complete the checkout process
    And I verify the ordered items integrate properly with warehouse management system
    And I confirm order status is correctly reflected in inventory system
    And I validate that order tracking integrates with shipping provider system
    Then all ordered items should be fully integrated into fulfillment ecosystem
    And inventory deductions and reservations should be applied correctly
    And the order should appear in all relevant system reports and customer portal
    And system performance should remain stable with increased transaction volume
