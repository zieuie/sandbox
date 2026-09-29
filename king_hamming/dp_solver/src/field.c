#include "kh_field.h"
#include "kh_threads.h"

#include <stdlib.h>
#include <string.h>

// Two-pass construction owns one counter slice per chunk and one shared field array.
typedef struct {
    const kh_parameters_t *parameters;
    const uint16_t *polynomial;
    uint32_t polynomial_packed;
    uint32_t threads;
    uint32_t *positions;
    uint32_t *cells;
    bool writing;
} field_context_t;

/*
 * Multiply two packed polynomial residues modulo the selected monic polynomial.
 * Parameters: parameters: Valid dimensions; polynomial: Reduction coefficients; left,right: Packed base-p elements.
 * Returns: The packed product in 0..q-1.
 */
static uint32_t multiply(const kh_parameters_t *parameters, const uint16_t *polynomial, uint32_t left, uint32_t right) {
    uint32_t a[32] = {0};
    uint32_t b[32] = {0};
    uint32_t product[64] = {0};
    uint32_t p = parameters->p;
    uint32_t r = parameters->r;

    // Decode exactly the r low-degree coefficients of each element.
    for (uint32_t index = 0; index < r; ++index) {
        a[index] = left % p;
        b[index] = right % p;
        left /= p;
        right /= p;
    }

    // Accumulate the convolution modulo p before any reduction.
    for (uint32_t i = 0; i < r; ++i) {
        for (uint32_t j = 0; j < r; ++j) {
            product[i + j] = (product[i + j] + a[i] * b[j]) % p;
        }
    }

    // Eliminate high coefficients from highest degree downwards.
    for (uint32_t degree = 2 * r - 2; degree >= r; --degree) {
        uint32_t factor = product[degree];

        for (uint32_t index = 0; index < r; ++index) {
            uint32_t offset = degree - r + index;
            product[offset] = (product[offset] + p - factor * polynomial[index] % p) % p;
        }
        product[degree] = 0;
    }
    uint64_t packed = 0;
    uint64_t power = 1;

    // Repack the reduced element within the checked q bound.
    for (uint32_t index = 0; index < r; ++index) {
        packed += product[index] * power;
        power *= p;
    }
    return (uint32_t)packed;
}

/*
 * Compute a power of X using quotient-ring multiplication.
 * Parameters: parameters: Valid dimensions; polynomial: Reduction coefficients; exponent: Nonnegative power.
 * Returns: Packed X^exponent, with no assumption that the polynomial is already primitive.
 */
static uint32_t power_x(const kh_parameters_t *parameters, const uint16_t *polynomial, uint32_t exponent) {
    uint32_t result = 1;
    uint32_t base = parameters->p;

    // Squaring handles the whole uint32 exponent range with bounded local scratch.
    while (exponent != 0) {
        if (exponent & 1) {
            result = multiply(parameters, polynomial, result, base);
        }
        exponent >>= 1;

        if (exponent != 0) {
            base = multiply(parameters, polynomial, base, base);
        }
    }
    return result;
}

/*
 * Verify exact generator order; q-1 distinct units force this q-element quotient to be a field.
 * Parameters: parameters: Valid dimensions; polynomial: r+1 input coefficients, low degree first.
 * Returns: True for a primitive-X monic polynomial, false otherwise.
 */
bool kh_primitive(const kh_parameters_t *parameters, const uint16_t *polynomial) {

    // The leading coefficient and constant term enforce the stated polynomial convention.
    if (polynomial[parameters->r] != 1 || polynomial[0] == 0) {
        return false;
    }

    for (uint32_t index = 0; index <= parameters->r; ++index) {
        if (polynomial[index] >= parameters->p) {
            return false;
        }
    }
    uint32_t order = parameters->q - 1;

    // X must be a unit whose order divides the desired multiplicative group order.
    if (power_x(parameters, polynomial, order) != 1) {
        return false;
    }
    uint32_t remaining = order;

    // Exclude each maximal proper divisor by trial-factoring the uint32 group order.
    for (uint32_t divisor = 2; divisor <= remaining / divisor; ++divisor) {
        if (remaining % divisor != 0) {
            continue;
        }

        if (power_x(parameters, polynomial, order / divisor) == 1) {
            return false;
        }

        do {
            remaining /= divisor;
        } while (remaining % divisor == 0);
    }

    if (remaining > 1 && power_x(parameters, polynomial, order / remaining) == 1) {
        return false;
    }
    return true;
}

/*
 * Enumerate lower coefficients in packed base-p order while fixing the monic leading term.
 * Parameters: parameters: Valid dimensions; start: First packed candidate; polynomial: r+1 output coefficients; chosen: Output candidate.
 * Returns: True for a found primitive-X polynomial; false if all remaining candidates fail.
 */
bool kh_generate_polynomial(const kh_parameters_t *parameters, uint32_t start, uint16_t *polynomial, uint32_t *chosen) {

    // Primitive polynomials have a nonzero constant coefficient.
    for (uint32_t candidate = start; candidate < parameters->q; ++candidate) {
        if (candidate % parameters->p == 0) {
            continue;
        }
        uint32_t packed = candidate;

        for (uint32_t index = 0; index < parameters->r; ++index) {
            polynomial[index] = (uint16_t)(packed % parameters->p);
            packed /= parameters->p;
        }
        polynomial[parameters->r] = 1;

        if (kh_primitive(parameters, polynomial)) {
            *chosen = candidate;
            return true;
        }
    }
    return false;
}

/*
 * Multiply by the fixed generator X without a general polynomial convolution.
 * Parameters: context: Construction state; element: Packed residue.
 * Returns: Packed X*element after one shift and reduction.
 */
static uint32_t next_power(const field_context_t *context, uint32_t element) {
    const kh_parameters_t *parameters = context->parameters;
    uint32_t p = parameters->p;
    uint32_t top_power = parameters->q / p;
    uint32_t carry = element / top_power;
    uint32_t shifted = (element % top_power) * p;

    // Binary packed coefficients are literal bits, so reduction is XOR.
    if (p == 2) {
        return shifted ^ (carry ? context->polynomial_packed : 0);
    }

    if (carry == 0) {
        return shifted;
    }
    uint64_t result = 0;
    uint64_t power = 1;

    // Reduce the carried X^r coefficient against every lower polynomial coefficient.
    for (uint32_t index = 0; index < parameters->r; ++index) {
        uint32_t digit = (uint32_t)(shifted / power % p);
        digit = (digit + p - carry * context->polynomial[index] % p) % p;
        result += digit * power;
        power *= p;
    }
    return (uint32_t)result;
}

/*
 * Map polynomial coefficients to the paper's leading prefix residue and low-degree suffix.
 * Parameters: parameters: Valid dimensions; element: Packed coefficient vector.
 * Returns: The SUD bucket index in 0..pF-1.
 */
static uint32_t cell_index(const kh_parameters_t *parameters, uint32_t element) {
    uint32_t suffix = element % parameters->f;
    uint32_t leading = element / parameters->f;
    uint32_t prefix = 0;

    // Binary residue sum is parity of exactly the leading coefficient block.
    if (parameters->p == 2) {
        prefix = (uint32_t)__builtin_parity(leading);
    } else {
        while (leading != 0) {
            prefix = (prefix + leading % parameters->p) % parameters->p;
            leading /= parameters->p;
        }
    }
    return prefix * parameters->f + suffix;
}

/*
 * Count or write one contiguous exponent-label chunk on a shared-pool antidiagonal.
 * Parameters: raw: Shared field_context_t; u,v: Pool coordinates selecting one unique chunk.
 * Returns: No value; writes only its counter slice or disjoint precomputed output ranges.
 */
static void fill_chunk(void *raw, uint32_t u, uint32_t v) {
    field_context_t *context = raw;

    // Other pool coordinates are empty; all actual chunks execute on one common diagonal.
    if (u + v != context->threads + 1) {
        return;
    }
    uint32_t chunk = u - 1;
    uint64_t nonzero = context->parameters->q - 1;
    uint32_t first = (uint32_t)(nonzero * chunk / context->threads + 1);
    uint32_t last = (uint32_t)(nonzero * (chunk + 1) / context->threads);
    uint32_t *positions = context->positions + (size_t)chunk * context->parameters->budget;
    uint32_t element = power_x(context->parameters, context->polynomial, first - 1);

    // Each chunk preserves ascending labels even when chunks execute in a different order.
    for (uint32_t label = first; label <= last; ++label) {
        uint32_t cell = cell_index(context->parameters, element);

        if (context->writing) {
            context->cells[positions[cell]++] = label;
        } else {
            ++positions[cell];
        }
        element = next_power(context, element);
    }
}

/*
 * Build one immutable SUD table with two parallel streaming passes and no log tables.
 * Parameters: parameters: Valid dimensions; polynomial: r+1 coefficients; threads: Worker count; max_bytes: Memory limit; output: Owned result; error: Failure diagnostic.
 * Returns: True on success; false after freeing any intermediate allocations.
 */
bool kh_build_field(const kh_parameters_t *parameters, const uint16_t *polynomial, uint32_t threads, uint64_t max_bytes, kh_field_t *output, const char **error) {
    memset(output, 0, sizeof *output);
    uint64_t cell_bytes = (uint64_t)parameters->q * sizeof(uint32_t);
    uint64_t position_bytes = (uint64_t)parameters->budget * threads * sizeof(uint32_t);
    uint64_t overhead = (uint64_t)threads * UINT64_C(8388608) + UINT64_C(67108864);

    // Reject invalid polynomials and incomplete memory budgets before any large allocation.
    if (threads == 0 || cell_bytes > SIZE_MAX || position_bytes > SIZE_MAX ||
        cell_bytes > max_bytes || position_bytes > max_bytes - cell_bytes ||
        overhead > max_bytes - cell_bytes - position_bytes || !kh_primitive(parameters, polynomial)) {
        *error = "invalid polynomial or field memory admission";
        return false;
    }
    uint32_t *cells = calloc((size_t)parameters->q, sizeof *cells);
    uint32_t *positions = calloc((size_t)parameters->budget * threads, sizeof *positions);

    if (cells == NULL || positions == NULL) {
        free(cells);
        free(positions);
        *error = "cannot allocate shared field state";
        return false;
    }
    field_context_t context = {parameters, polynomial, 0, threads, positions, cells, false};
    uint64_t power = 1;

    // A packed lower polynomial also supports the constant-time binary reduction path.
    for (uint32_t index = 0; index < parameters->r; ++index) {
        context.polynomial_packed += (uint32_t)(polynomial[index] * power);
        power *= parameters->p;
    }
    kh_pool_t *pool = kh_pool_create(threads, fill_chunk, &context, error);

    if (pool == NULL) {
        free(cells);
        free(positions);
        return false;
    }
    positions[0] = 1;
    kh_pool_fill(pool, 1, threads, 1, threads);
    bool valid = true;

    // Prefix sums concatenate label chunks in original label order within each SUD bucket.
    for (uint32_t cell = 0; cell < parameters->budget; ++cell) {
        uint64_t offset = (uint64_t)cell * parameters->f;
        uint64_t total = 0;

        for (uint32_t chunk = 0; chunk < threads; ++chunk) {
            size_t index = (size_t)chunk * parameters->budget + cell;
            uint32_t count = positions[index];
            positions[index] = (uint32_t)offset;
            offset += count;
            total += count;
        }

        if (total != parameters->f) {
            valid = false;
            break;
        }
    }

    // Zero occupies the first cell entry and precedes all nonzero exponent labels.
    if (valid) {
        ++positions[0];
        context.writing = true;
        kh_pool_fill(pool, 1, threads, 1, threads);
    }
    kh_pool_destroy(pool);
    free(positions);

    if (!valid) {
        free(cells);
        *error = "field partition cardinality invariant failed";
        return false;
    }
    output->parameters = *parameters;
    memcpy(output->polynomial, polynomial, ((size_t)parameters->r + 1) * sizeof *polynomial);
    output->cells = cells;
    output->allocated_bytes = cell_bytes;
    return true;
}

/*
 * Release and clear shared field ownership.
 * Parameters: field: Initialized or zero-initialized field structure.
 * Returns: No value; clears the cells pointer after freeing it.
 */
void kh_free_field(kh_field_t *field) {
    free(field->cells);
    field->cells = NULL;
}
